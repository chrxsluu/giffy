/* giffy's hot loops: shared palette, dithering + lossy LZW, exact mapping.
 *
 * Every function releases the GIL while it works, so Python threads run them in
 * parallel (one frame per thread). Output never depends on the thread count.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define MAX_SIDE 65535                 /* GIF stores sizes as 16-bit */
#define LUT_SIZE (1 << 18)             /* nearest-colour table: 6 bits per channel */
#define KMEANS_ITERS 1

/* ------------------------------------------------------------------ palette */

typedef struct { int n; int32_t r[256], g[256], b[256]; float fr[256], fg[256], fb[256]; } Pal;

static int load_pal(const Py_buffer *pb, Pal *p) {
    if (pb->len < 3 || pb->len > 768 || pb->len % 3) {
        PyErr_SetString(PyExc_ValueError, "palette must hold 1 to 256 RGB colours");
        return -1;
    }
    const uint8_t *s = pb->buf;
    p->n = (int)(pb->len / 3);
    for (int i = 0; i < p->n; i++) {
        p->r[i] = s[3 * i]; p->g[i] = s[3 * i + 1]; p->b[i] = s[3 * i + 2];
        p->fr[i] = (float)p->r[i]; p->fg[i] = (float)p->g[i]; p->fb[i] = (float)p->b[i];
    }
    return 0;
}

/* Smallest of d[0..n), as 8 independent lanes so it vectorises (also on plain SSE2). */
static inline float min_of(const float *d, int n) {
    float m[8] = {FLT_MAX, FLT_MAX, FLT_MAX, FLT_MAX, FLT_MAX, FLT_MAX, FLT_MAX, FLT_MAX};
    int i = 0;
    for (; i + 8 <= n; i += 8)
        for (int j = 0; j < 8; j++) m[j] = d[i + j] < m[j] ? d[i + j] : m[j];
    for (; i < n; i++) m[0] = d[i] < m[0] ? d[i] : m[0];
    for (int j = 1; j < 8; j++) m[0] = m[j] < m[0] ? m[j] : m[0];
    return m[0];
}

/* Index of the closest palette colour (squared RGB distance; ties -> lowest index).
 * Floats hold these integer distances (< 2^24) exactly and multiply fast everywhere. */
static int nearest(const Pal *p, int r, int g, int b) {
    float d[256];
    const float fr = (float)r, fg = (float)g, fb = (float)b;
    const int n = p->n;
    for (int i = 0; i < n; i++) {
        float dr = p->fr[i] - fr, dg = p->fg[i] - fg, db = p->fb[i] - fb;
        d[i] = dr * dr + dg * dg + db * db;
    }
    const float m = min_of(d, n);
    int i = 0;
    while (d[i] != m) i++;
    return i;
}

/* Shared between threads. A slot holds 0xFFFF until someone fills it with the
 * nearest colour to the bin's centre; the value depends only on the bin, so
 * racing fills write the same number (relaxed atomics keep that well-defined). */
static inline int lut_get(uint16_t *lut, const Pal *p, int r, int g, int b) {
    int key = ((r >> 2) << 12) | ((g >> 2) << 6) | (b >> 2);
    uint16_t v = __atomic_load_n(&lut[key], __ATOMIC_RELAXED);
    if (v == 0xFFFF) {
        v = (uint16_t)nearest(p, (r & ~3) | 2, (g & ~3) | 2, (b & ~3) | 2);
        __atomic_store_n(&lut[key], v, __ATOMIC_RELAXED);
    }
    return v;
}

/* Small open-addressing set of 24-bit colours, for the exact-palette path. */
#define CSET 1024
typedef struct { uint32_t key[CSET]; uint8_t idx[CSET]; int n; } ColorSet;

static inline uint32_t chash(uint32_t c) { return (c * 2654435761u) >> 22; }

/* 1 = added, 0 = already there, -1 = set full (more than `cap` colours). */
static int cset_add(ColorSet *s, uint32_t c, int cap) {
    uint32_t h = chash(c), k = c + 1;
    while (s->key[h]) {
        if (s->key[h] == k) return 0;
        h = (h + 1) & (CSET - 1);
    }
    if (s->n == cap) return -1;
    s->key[h] = k;
    s->idx[h] = (uint8_t)s->n++;
    return 1;
}

static int cset_find(const ColorSet *s, uint32_t c) {
    uint32_t h = chash(c), k = c + 1;
    while (s->key[h]) {
        if (s->key[h] == k) return s->idx[h];
        h = (h + 1) & (CSET - 1);
    }
    return -1;
}

static uint64_t gcd(uint64_t a, uint64_t b) {
    while (b) { uint64_t t = a % b; a = b; b = t; }
    return a;
}

static int cmp_u32(const void *a, const void *b) {
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return (x > y) - (x < y);
}

/* Wu's colour quantizer on a 32-level-per-channel histogram. */
typedef struct { int64_t n, r, g, b, q; } Mom;
typedef struct { int lo[3], hi[3]; double sse; } Box;   /* lo exclusive, hi inclusive, 0..32 */

#define MI(r, g, b) (((r) * 33 + (g)) * 33 + (b))

static Mom vol(const Mom *m, const Box *x) {
    const int r0 = x->lo[0], r1 = x->hi[0], g0 = x->lo[1], g1 = x->hi[1], b0 = x->lo[2], b1 = x->hi[2];
    const Mom *a = &m[MI(r1, g1, b1)], *b = &m[MI(r1, g1, b0)], *c = &m[MI(r1, g0, b1)], *d = &m[MI(r1, g0, b0)];
    const Mom *e = &m[MI(r0, g1, b1)], *f = &m[MI(r0, g1, b0)], *g = &m[MI(r0, g0, b1)], *h = &m[MI(r0, g0, b0)];
    Mom v;
#define V(F) v.F = a->F - b->F - c->F + d->F - e->F + f->F + g->F - h->F
    V(n); V(r); V(g); V(b); V(q);
#undef V
    return v;
}

static double mom_sse(Mom v) {
    if (v.n == 0) return 0;
    return (double)v.q - ((double)v.r * v.r + (double)v.g * v.g + (double)v.b * v.b) / (double)v.n;
}

static double mom_score(Mom v) {   /* bigger = tighter cluster; summed over both halves */
    return ((double)v.r * v.r + (double)v.g * v.g + (double)v.b * v.b) / (double)v.n;
}

/* Split box *x at its best cut into *x and *y. 0 if no cut leaves both halves non-empty. */
static int wu_cut(const Mom *m, Box *x, Box *y) {
    Mom whole = vol(m, x);
    double best = -1;
    int bax = -1, bcut = 0;
    for (int ax = 0; ax < 3; ax++) {
        for (int cut = x->lo[ax] + 1; cut < x->hi[ax]; cut++) {
            Box l = *x;
            l.hi[ax] = cut;
            Mom lv = vol(m, &l), rv;
            rv.n = whole.n - lv.n; rv.r = whole.r - lv.r; rv.g = whole.g - lv.g; rv.b = whole.b - lv.b;
            if (lv.n == 0 || rv.n == 0) continue;
            double s = mom_score(lv) + mom_score(rv);
            if (s > best) { best = s; bax = ax; bcut = cut; }
        }
    }
    if (bax < 0) return 0;
    *y = *x;
    x->hi[bax] = bcut;
    y->lo[bax] = bcut;
    x->sse = mom_sse(vol(m, x));
    y->sse = mom_sse(vol(m, y));
    return 1;
}

typedef struct { int n; uint8_t rgb[768]; int exact; int err; } PalOut;

static void build_palette(const uint8_t **px, int nf, size_t npix, int w, int k, PalOut *o) {
    o->err = 0;
    /* 1. Few enough colours? Then the palette is exactly those, sorted (lossless). */
    ColorSet *cs = calloc(1, sizeof *cs);
    if (!cs) { o->err = 1; return; }
    int exact = 1;
    for (int f = 0; f < nf && exact; f++) {
        const uint8_t *p = px[f];
        uint32_t prev = 0xFFFFFFFF;
        for (size_t i = 0; i < npix; i++, p += 3) {
            uint32_t c = ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | p[2];
            if (c == prev) continue;
            prev = c;
            if (cset_add(cs, c, k) < 0) { exact = 0; break; }
        }
    }
    if (exact) {
        uint32_t cols[256];
        int n = 0;
        for (int i = 0; i < CSET; i++) if (cs->key[i]) cols[n++] = cs->key[i] - 1;
        qsort(cols, n, sizeof cols[0], cmp_u32);
        for (int i = 0; i < n; i++) {
            o->rgb[3 * i] = cols[i] >> 16; o->rgb[3 * i + 1] = (cols[i] >> 8) & 255; o->rgb[3 * i + 2] = cols[i] & 255;
        }
        o->n = n;
        o->exact = 1;
        free(cs);
        return;
    }
    free(cs);
    o->exact = 0;

    /* 2. Histogram of a ~1M-pixel sample, 5 bits per channel, with exact sums. */
    Mom *hist = calloc(32 * 32 * 32, sizeof *hist), *m = calloc(33 * 33 * 33, sizeof *m);
    Box *boxes = malloc(sizeof(Box) * k);
    if (!hist || !m || !boxes) { free(hist); free(m); free(boxes); o->err = 1; return; }
    /* every stride-th pixel of the frames laid end to end; a stride sharing no
     * factor with the width walks through every column instead of a few */
    const uint64_t total = (uint64_t)npix * (uint64_t)nf;
    uint64_t stride = total >> 20, j = 0;
    if (stride < 1) stride = 1;
    while (gcd(stride, (uint64_t)w) != 1) stride++;
    for (int f = 0; f < nf; f++, j -= npix) {
        for (; j < npix; j += stride) {
            const uint8_t *q = px[f] + 3 * j;
            int r = q[0], g = q[1], b = q[2];
            Mom *c = &hist[((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)];
            c->n++; c->r += r; c->g += g; c->b += b; c->q += r * r + g * g + b * b;
        }
    }
    /* cumulative moments (Wu 1991) */
    for (int r = 1; r <= 32; r++) {
        Mom area[33];
        memset(area, 0, sizeof area);
        for (int g = 1; g <= 32; g++) {
            Mom line = {0, 0, 0, 0, 0};
            for (int b = 1; b <= 32; b++) {
                const Mom *c = &hist[((r - 1) << 10) | ((g - 1) << 5) | (b - 1)];
                line.n += c->n; line.r += c->r; line.g += c->g; line.b += c->b; line.q += c->q;
                area[b].n += line.n; area[b].r += line.r; area[b].g += line.g; area[b].b += line.b; area[b].q += line.q;
                const Mom *u = &m[MI(r - 1, g, b)];
                Mom *d = &m[MI(r, g, b)];
                d->n = u->n + area[b].n; d->r = u->r + area[b].r; d->g = u->g + area[b].g;
                d->b = u->b + area[b].b; d->q = u->q + area[b].q;
            }
        }
    }
    /* 3. Keep splitting the box with the most error until there are k. */
    int nb = 1;
    boxes[0] = (Box){{0, 0, 0}, {32, 32, 32}, 0};
    boxes[0].sse = mom_sse(vol(m, &boxes[0]));
    while (nb < k) {
        int pick = -1;
        double worst = 0;
        for (int i = 0; i < nb; i++) if (boxes[i].sse > worst) { worst = boxes[i].sse; pick = i; }
        if (pick < 0) break;                         /* every box is a single colour */
        if (!wu_cut(m, &boxes[pick], &boxes[nb])) { boxes[pick].sse = 0; continue; }
        nb++;
    }
    /* 4. Box means, then k-means over the occupied bins to tighten them. */
    float cr[256], cg[256], cb[256];
    for (int i = 0; i < nb; i++) {
        Mom v = vol(m, &boxes[i]);
        cr[i] = (float)((double)v.r / v.n); cg[i] = (float)((double)v.g / v.n); cb[i] = (float)((double)v.b / v.n);
    }
    int nbins = 0;
    for (int i = 0; i < 32 * 32 * 32; i++) if (hist[i].n) hist[nbins++] = hist[i];   /* compact in place */
    float *mean = malloc(sizeof(float) * 3 * (size_t)nbins);
    if (!mean) { free(hist); free(m); free(boxes); o->err = 1; return; }
    for (int i = 0; i < nbins; i++) {
        mean[3 * i] = (float)((double)hist[i].r / hist[i].n);
        mean[3 * i + 1] = (float)((double)hist[i].g / hist[i].n);
        mean[3 * i + 2] = (float)((double)hist[i].b / hist[i].n);
    }
    for (int it = 0; it < KMEANS_ITERS; it++) {
        int64_t sr[256] = {0}, sg[256] = {0}, sb[256] = {0}, sn[256] = {0};
        for (int i = 0; i < nbins; i++) {
            const Mom *hb = &hist[i];
            const float r = mean[3 * i], g = mean[3 * i + 1], b = mean[3 * i + 2];
            float d[256];
            for (int c = 0; c < nb; c++) {
                float dr = cr[c] - r, dg = cg[c] - g, db = cb[c] - b;
                d[c] = dr * dr + dg * dg + db * db;
            }
            const float bd = min_of(d, nb);
            int best = 0;
            while (d[best] != bd) best++;
            sr[best] += hb->r; sg[best] += hb->g; sb[best] += hb->b; sn[best] += hb->n;
        }
        for (int c = 0; c < nb; c++) if (sn[c] > 0) {
            cr[c] = (float)((double)sr[c] / sn[c]); cg[c] = (float)((double)sg[c] / sn[c]);
            cb[c] = (float)((double)sb[c] / sn[c]);
        }
    }
    for (int i = 0; i < nb; i++) {   /* centroids stay within 0..255: they are means of pixels */
        o->rgb[3 * i] = (uint8_t)(cr[i] + 0.5f); o->rgb[3 * i + 1] = (uint8_t)(cg[i] + 0.5f);
        o->rgb[3 * i + 2] = (uint8_t)(cb[i] + 0.5f);
    }
    o->n = nb;
    free(mean); free(hist); free(m); free(boxes);
}

static int check_size(Py_ssize_t w, Py_ssize_t h) {
    if (w < 1 || h < 1 || w > MAX_SIDE || h > MAX_SIDE) {
        PyErr_Format(PyExc_ValueError, "image size %zdx%zd is outside 1..%d", w, h, MAX_SIDE);
        return -1;
    }
    return 0;
}

static PyObject *py_palette(PyObject *self, PyObject *args) {
    PyObject *seq;
    Py_ssize_t w, h;
    int k;
    if (!PyArg_ParseTuple(args, "Onni", &seq, &w, &h, &k)) return NULL;
    if (check_size(w, h) < 0) return NULL;
    if (k < 1 || k > 256) { PyErr_SetString(PyExc_ValueError, "colours must be 1..256"); return NULL; }
    PyObject *fast = PySequence_Fast(seq, "frames must be a sequence of RGB buffers");
    if (!fast) return NULL;
    Py_ssize_t nf = PySequence_Fast_GET_SIZE(fast);
    if (nf < 1) { Py_DECREF(fast); PyErr_SetString(PyExc_ValueError, "no frames"); return NULL; }
    Py_buffer *bufs = calloc(nf, sizeof *bufs);
    const uint8_t **px = calloc(nf, sizeof *px);
    PyObject *res = NULL;
    Py_ssize_t got = 0;
    if (!bufs || !px) { PyErr_NoMemory(); goto done; }
    const size_t npix = (size_t)w * (size_t)h;   /* only trusted once a buffer of 3x that exists */
    for (; got < nf; got++) {
        if (PyObject_GetBuffer(PySequence_Fast_GET_ITEM(fast, got), &bufs[got], PyBUF_SIMPLE) < 0) goto done;
        if ((uint64_t)bufs[got].len != (uint64_t)w * (uint64_t)h * 3) {
            PyBuffer_Release(&bufs[got]);
            PyErr_SetString(PyExc_ValueError, "frame buffer is not w*h*3 bytes");
            goto done;
        }
        px[got] = bufs[got].buf;
    }
    PalOut o;
    Py_BEGIN_ALLOW_THREADS
    build_palette(px, (int)nf, npix, (int)w, k, &o);
    Py_END_ALLOW_THREADS
    if (o.err) { PyErr_NoMemory(); goto done; }
    res = Py_BuildValue("(y#O)", (const char *)o.rgb, (Py_ssize_t)o.n * 3, o.exact ? Py_True : Py_False);
done:
    for (Py_ssize_t i = 0; i < got; i++) PyBuffer_Release(&bufs[i]);
    free(bufs); free(px);
    Py_DECREF(fast);
    return res;
}

/* ------------------------------------------------------------- exact remap */

/* Palette index of every pixel, for palettes that hold every colour in the shot. */
static PyObject *py_remap(PyObject *self, PyObject *args) {
    Py_buffer rgb, palb;
    Py_ssize_t w, h;
    if (!PyArg_ParseTuple(args, "y*nny*", &rgb, &w, &h, &palb)) return NULL;
    PyObject *out = NULL;
    Pal p;
    if (check_size(w, h) < 0 || load_pal(&palb, &p) < 0) goto done;
    if ((uint64_t)rgb.len != (uint64_t)w * (uint64_t)h * 3) { PyErr_SetString(PyExc_ValueError, "rgb buffer is not w*h*3 bytes"); goto done; }
    const size_t npix = (size_t)w * (size_t)h;
    out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)npix);
    ColorSet *cs = calloc(1, sizeof *cs);
    if (!out || !cs) { free(cs); Py_CLEAR(out); if (!PyErr_Occurred()) PyErr_NoMemory(); goto done; }
    uint8_t *o = (uint8_t *)PyBytes_AS_STRING(out);
    const uint8_t *s = rgb.buf;
    Py_BEGIN_ALLOW_THREADS
    for (int i = 0; i < p.n; i++) cset_add(cs, ((uint32_t)p.r[i] << 16) | ((uint32_t)p.g[i] << 8) | (uint32_t)p.b[i], 256);
    for (size_t i = 0; i < npix; i++, s += 3) {
        int k = cset_find(cs, ((uint32_t)s[0] << 16) | ((uint32_t)s[1] << 8) | s[2]);
        o[i] = (uint8_t)(k >= 0 ? k : nearest(&p, s[0], s[1], s[2]));
    }
    Py_END_ALLOW_THREADS
    free(cs);
done:
    PyBuffer_Release(&rgb); PyBuffer_Release(&palb);
    return out;
}

/* ---------------------------------------------------------------------- lzw */

#define HBITS 14
#define HSIZE (1 << HBITS)
#define NONE 0xFFFF

typedef struct { uint8_t *out; size_t pos, blk; int cnt; uint64_t acc; int nbits; } BitW;

static inline void put_byte(BitW *b, uint8_t v) {
    b->out[b->pos++] = v;
    if (++b->cnt == 255) { b->out[b->blk] = 255; b->blk = b->pos++; b->cnt = 0; }
}

static inline void emit(BitW *b, uint32_t code, int size) {   /* GIF packs codes LSB first */
    b->acc |= (uint64_t)code << b->nbits;
    b->nbits += size;
    while (b->nbits >= 8) { put_byte(b, (uint8_t)b->acc); b->acc >>= 8; b->nbits -= 8; }
}

typedef struct {
    BitW b;
    uint32_t clear, eoi, nxt, gen, w;   /* w: code of the string so far */
    int size, min;
    uint32_t *keys;                     /* (gen << 20 | w << 8 | c), other generations = empty */
    uint16_t *codes;
    uint16_t *first, *next;             /* optional child lists: first[w], next[child] */
    uint8_t *sym;                       /* last index of each code's string */
} Lzw;

typedef struct { uint32_t keys[HSIZE]; uint16_t codes[HSIZE], first[4096], next[4096]; uint8_t sym[4096]; } LzwMem;

static void lzw_start(Lzw *z, int min, uint8_t *out, LzwMem *m, int children) {
    z->min = min; z->clear = 1u << min; z->eoi = z->clear + 1; z->nxt = z->eoi + 1; z->gen = 1;
    z->size = min + 1; z->keys = m->keys; z->codes = m->codes;
    z->first = children ? m->first : NULL; z->next = m->next; z->sym = m->sym;
    memset(m->keys, 0, sizeof m->keys);
    if (children) memset(m->first, 0xFF, sizeof m->first);
    z->b = (BitW){out, 2, 1, 0, 0, 0};
    out[0] = (uint8_t)min;
    emit(&z->b, z->clear, z->size);
}

/* Code for "string so far + c", or -1. *slot gets where to add it. */
static inline int lzw_child(const Lzw *z, uint32_t c, uint32_t *slot) {
    const uint32_t key = (z->w << 8) | c, tag = (z->gen << 20) | key;
    uint32_t hp = (key * 2654435761u) >> (32 - HBITS), s;
    while (((s = z->keys[hp]) >> 20) == z->gen) {
        if (s == tag) return z->codes[hp];
        hp = (hp + 1) & (HSIZE - 1);
    }
    *slot = hp;
    return -1;
}

/* The string can't grow by c: send it, learn "string + c", start again from c. */
static inline void lzw_add(Lzw *z, uint32_t c, uint32_t slot) {
    emit(&z->b, z->w, z->size);
    const uint32_t code = z->nxt++;
    z->keys[slot] = (z->gen << 20) | (z->w << 8) | c;
    z->codes[slot] = (uint16_t)code;
    if (z->first) { z->sym[code] = (uint8_t)c; z->next[code] = z->first[z->w]; z->first[z->w] = (uint16_t)code; }
    if (z->nxt > (1u << z->size) && z->size < 12) z->size++;
    if (z->nxt == 4096) {                            /* table full: start over */
        emit(&z->b, z->clear, z->size);
        z->size = z->min + 1;
        z->nxt = z->eoi + 1;
        if (++z->gen == 4096) { memset(z->keys, 0, sizeof *z->keys * HSIZE); z->gen = 1; }
        if (z->first) memset(z->first, 0xFF, sizeof *z->first * 4096);
    }
    z->w = c;
}

/* Returns bytes written: min code size, sub-blocks, terminator. */
static size_t lzw_finish(Lzw *z) {
    BitW *b = &z->b;
    emit(b, z->w, z->size);
    /* the decoder adds one more table entry on reading w, which may widen the next code */
    emit(b, z->eoi, (z->nxt == (1u << z->size) && z->size < 12) ? z->size + 1 : z->size);
    if (b->nbits) put_byte(b, (uint8_t)b->acc);
    if (b->cnt) { b->out[b->blk] = (uint8_t)b->cnt; b->out[b->pos++] = 0; }
    else { b->out[b->blk] = 0; b->pos = b->blk + 1; }
    return b->pos;
}

/* Bytes needed for the LZW output of n pixels (<= one 12-bit code per pixel, plus clears). */
static size_t lzw_cap(size_t n) {
    size_t raw = (12 * (n + n / 3000 + 8)) / 8 + 8;
    return raw + raw / 255 + 16;
}

/* Returns 0 if an index doesn't fit min code size. */
static size_t lzw_encode(const uint8_t *idx, size_t n, int min, uint8_t *out, LzwMem *m) {
    Lzw z;
    lzw_start(&z, min, out, m, 0);
    z.w = idx[0];
    if (z.w >= z.clear) return 0;
    for (size_t i = 1; i < n; i++) {
        const uint32_t c = idx[i];
        if (c >= z.clear) return 0;
        uint32_t slot;
        int code = lzw_child(&z, c, &slot);
        if (code >= 0) z.w = (uint32_t)code;
        else lzw_add(&z, c, slot);
    }
    return lzw_finish(&z);
}

static PyObject *py_lzw(PyObject *self, PyObject *args) {
    Py_buffer ib;
    int min;
    if (!PyArg_ParseTuple(args, "y*i", &ib, &min)) return NULL;
    PyObject *out = NULL;
    LzwMem *m = NULL;
    if (min < 2 || min > 8) { PyErr_SetString(PyExc_ValueError, "min code size must be 2..8"); goto done; }
    if (ib.len < 1) { PyErr_SetString(PyExc_ValueError, "no pixels"); goto done; }
    size_t n = (size_t)ib.len, cap = lzw_cap(n);
    m = malloc(sizeof *m);
    if (!m || cap > (size_t)PY_SSIZE_T_MAX) { PyErr_NoMemory(); goto done; }
    out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)cap);
    if (!out) goto done;
    size_t len;
    Py_BEGIN_ALLOW_THREADS
    len = lzw_encode(ib.buf, n, min, (uint8_t *)PyBytes_AS_STRING(out), m);
    Py_END_ALLOW_THREADS
    if (!len) { Py_CLEAR(out); PyErr_SetString(PyExc_ValueError, "index too big for the min code size"); goto done; }
    _PyBytes_Resize(&out, (Py_ssize_t)len);
done:
    free(m);
    PyBuffer_Release(&ib);
    return out;
}

/* ---------------------------------------------------- dither + lzw in one pass */

static inline int32_t dist2(const Pal *p, int k, const int v[3]) {
    int32_t dr = p->r[k] - v[0], dg = p->g[k] - v[1], db = p->b[k] - v[2];
    return dr * dr + dg * dg + db * db;
}

/* Floyd-Steinberg (error x strength/16) in reading order, feeding LZW as it goes.
 * With lossy > 0, a pixel whose nearest colour would end the current LZW string
 * may instead take any colour that continues it, if that colour is at most
 * `lossy` RGB units further from the pixel's target; its error is diffused like
 * any other, so the area keeps its colour on average. */
static int encode_dither(const uint8_t *rgb, int w, int h, const Pal *p, uint16_t *lut, int min,
                         float lossy, int strength, uint8_t *out, size_t *len) {
    int32_t *e0 = calloc((size_t)(w + 2) * 3, sizeof *e0), *e1 = calloc((size_t)(w + 2) * 3, sizeof *e1);
    LzwMem *m = malloc(sizeof *m);
    if (!e0 || !e1 || !m) { free(e0); free(e1); free(m); return -1; }
    /* Floyd-Steinberg weights 7, 3, 5, 1 (/16), times strength/16: errors are x256 */
    const int32_t w7 = 7 * strength, w3 = 3 * strength, w5 = 5 * strength, w1 = strength;
    Lzw z;
    lzw_start(&z, min, out, m, lossy > 0);
    for (int y = 0; y < h; y++) {
        const uint8_t *s = rgb + (size_t)y * w * 3;
        const int32_t *in = e0 + 3;   /* error for this row, x256, column x at in[3x] */
        int32_t *nx = e1 + 3;         /* error for the next row; column -1 is padding */
        /* in registers: 7/16 from the left neighbour (f*), and the next row's
         * running sums for columns x-1 (a*) and x (b*) */
        int32_t fr = 0, fg = 0, fb = 0, ar = 0, ag = 0, ab = 0, br = 0, bg = 0, bb = 0;
        for (int x = 0; x < w; x++, s += 3, in += 3) {
            int v[3];
            int t;
            t = s[0] + ((in[0] + fr + 128) >> 8); v[0] = t < 0 ? 0 : t > 255 ? 255 : t;
            t = s[1] + ((in[1] + fg + 128) >> 8); v[1] = t < 0 ? 0 : t > 255 ? 255 : t;
            t = s[2] + ((in[2] + fb + 128) >> 8); v[2] = t < 0 ? 0 : t > 255 ? 255 : t;
            int k = lut_get(lut, p, v[0], v[1], v[2]);
            if (x == 0 && y == 0) {
                z.w = (uint32_t)k;
            } else {
                uint32_t slot;
                int code = lzw_child(&z, (uint32_t)k, &slot);
                if (code < 0 && z.first) {
                    const float lim = sqrtf((float)dist2(p, k, v)) + lossy;
                    int32_t bd = (int32_t)(lim * lim) + 1;
                    for (uint32_t ch = z.first[z.w]; ch != NONE; ch = z.next[ch]) {
                        const int32_t dc = dist2(p, z.sym[ch], v);
                        if (dc < bd) { bd = dc; code = (int)ch; }
                    }
                    if (code >= 0) k = z.sym[code];
                }
                if (code >= 0) z.w = (uint32_t)code;
                else lzw_add(&z, (uint32_t)k, slot);
            }
            const int32_t er = v[0] - p->r[k], eg = v[1] - p->g[k], eb = v[2] - p->b[k];
            fr = w7 * er; fg = w7 * eg; fb = w7 * eb;                   /* right */
            int32_t *o = nx + 3 * (x - 1);                              /* down-left is now complete */
            o[0] = ar + w3 * er; o[1] = ag + w3 * eg; o[2] = ab + w3 * eb;
            ar = br + w5 * er; ag = bg + w5 * eg; ab = bb + w5 * eb;    /* down */
            br = w1 * er; bg = w1 * eg; bb = w1 * eb;                   /* down-right */
        }
        int32_t *o = nx + 3 * (w - 1);
        o[0] = ar; o[1] = ag; o[2] = ab;
        int32_t *tmp = e0; e0 = e1; e1 = tmp;
    }
    *len = lzw_finish(&z);
    free(e0); free(e1); free(m);
    return 0;
}

static PyObject *py_encode(PyObject *self, PyObject *args) {
    Py_buffer rgb, palb, lutb;
    Py_ssize_t w, h;
    int min, strength;
    double lossy;
    if (!PyArg_ParseTuple(args, "y*nny*w*idi", &rgb, &w, &h, &palb, &lutb, &min, &lossy, &strength)) return NULL;
    PyObject *out = NULL;
    Pal p;
    if (check_size(w, h) < 0 || load_pal(&palb, &p) < 0) goto done;
    if ((uint64_t)rgb.len != (uint64_t)w * (uint64_t)h * 3) { PyErr_SetString(PyExc_ValueError, "rgb buffer is not w*h*3 bytes"); goto done; }
    if (lutb.len != LUT_SIZE * 2 || ((uintptr_t)lutb.buf & 1)) { PyErr_SetString(PyExc_ValueError, "bad lut buffer"); goto done; }
    if (min < 2 || min > 8 || p.n > (1 << min)) { PyErr_SetString(PyExc_ValueError, "min code size doesn't fit the palette"); goto done; }
    if (!(lossy >= 0 && lossy <= 255)) { PyErr_SetString(PyExc_ValueError, "lossy must be 0..255"); goto done; }
    if (strength < 0 || strength > 16) { PyErr_SetString(PyExc_ValueError, "strength must be 0..16"); goto done; }
    out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)lzw_cap((size_t)w * (size_t)h));
    if (!out) goto done;
    size_t len = 0;
    int err;
    Py_BEGIN_ALLOW_THREADS
    err = encode_dither(rgb.buf, (int)w, (int)h, &p, lutb.buf, min, (float)lossy, strength,
                        (uint8_t *)PyBytes_AS_STRING(out), &len);
    Py_END_ALLOW_THREADS
    if (err) { Py_CLEAR(out); PyErr_NoMemory(); goto done; }
    _PyBytes_Resize(&out, (Py_ssize_t)len);
done:
    PyBuffer_Release(&rgb); PyBuffer_Release(&palb); PyBuffer_Release(&lutb);
    return out;
}

/* ------------------------------------------------------------------- module */

static PyMethodDef methods[] = {
    {"palette", py_palette, METH_VARARGS,
     "palette(frames, w, h, k) -> (rgb bytes, exact). One shared palette of <= k colours."},
    {"encode", py_encode, METH_VARARGS,
     "encode(rgb, w, h, palette, lut, min_code_size, lossy, strength) -> GIF image data.\n"
     "Dithers (error x strength/16) and LZW-encodes in one pass; lossy > 0 trades error for size."},
    {"remap", py_remap, METH_VARARGS,
     "remap(rgb, w, h, palette) -> index bytes, for a palette holding every colour (else nearest)."},
    {"lzw", py_lzw, METH_VARARGS,
     "lzw(indices, min_code_size) -> GIF image data (code size, sub-blocks, terminator)."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef mod = {PyModuleDef_HEAD_INIT, .m_name = "_giffy", .m_size = -1, .m_methods = methods};

PyMODINIT_FUNC PyInit__giffy(void) {
    PyObject *m = PyModule_Create(&mod);
    if (m && PyModule_AddIntConstant(m, "LUT_SIZE", LUT_SIZE) < 0) { Py_DECREF(m); return NULL; }
    return m;
}
