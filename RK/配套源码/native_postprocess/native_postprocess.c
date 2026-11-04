#define _POSIX_C_SOURCE 200809L
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>

typedef struct {
    double box[4];
    float objectness;
    uint32_t index;
} candidate;

typedef struct {
    uint32_t height, width, classes, grid;
    float *heat, *scores;
    candidate *candidates;
} postprocess;

static double clock_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec * 1000.0 + (double)t.tv_nsec / 1000000.0;
}

static float sigmoid(float z) {
    float e = expf(-fabsf(z));
    return z >= 0.0f ? 1.0f / (1.0f + e) : e / (1.0f + e);
}

static int ordered(const void *left, const void *right) {
    const candidate *a = left, *b = right;
    if (a->objectness > b->objectness)
        return -1;
    if (a->objectness < b->objectness)
        return 1;
    return a->index < b->index ? -1 : a->index > b->index;
}

static double overlap(const candidate *a, const candidate *b) {
    double left = fmax(a->box[0], b->box[0]), top = fmax(a->box[1], b->box[1]);
    double right = fmin(a->box[2], b->box[2]), bottom = fmin(a->box[3], b->box[3]);
    double intersection = fmax(0.0, right - left) * fmax(0.0, bottom - top);
    double area_a = fmax(0.0, a->box[2] - a->box[0]) * fmax(0.0, a->box[3] - a->box[1]);
    double area_b = fmax(0.0, b->box[2] - b->box[0]) * fmax(0.0, b->box[3] - b->box[1]);
    return intersection / fmax(1e-9, area_a + area_b - intersection);
}

void native_postprocess_close(void *handle) {
    postprocess *p = handle;
    if (!p)
        return;
    free(p->heat);
    free(p->scores);
    free(p->candidates);
    free(p);
}

void *native_postprocess_open(uint32_t height, uint32_t width, uint32_t classes) {
    uint64_t grid = (uint64_t)height * width;
    if (!grid || grid > 1024U * 1024U || !classes || classes > 256)
        return NULL;
    postprocess *p = calloc(1, sizeof(*p));
    if (!p)
        return NULL;
    p->height = height;
    p->width = width;
    p->classes = classes;
    p->grid = (uint32_t)grid;
    p->heat = malloc((size_t)grid * sizeof(float));
    p->scores = malloc(classes * sizeof(float));
    p->candidates = malloc((size_t)grid * sizeof(candidate));
    if (!p->heat || !p->scores || !p->candidates) {
        native_postprocess_close(p);
        return NULL;
    }
    return p;
}

int native_postprocess_decode(void *handle, const float *raw, uint32_t elements,
                              double object_threshold, double *rows, double *elapsed_ms) {
    postprocess *p = handle;
    if (!p || !raw || !rows || !elapsed_ms)
        return -1;
    uint64_t expected = (uint64_t)(5U + p->classes) * p->grid;
    if (elements != expected)
        return -2;
    double started = clock_ms();
    for (uint32_t i = 0; i < elements; ++i)
        if (!isfinite(raw[i]))
            return -3;
    for (uint32_t i = 0; i < p->grid; ++i)
        p->heat[i] = sigmoid(raw[i]);
    uint32_t count = 0;
    float threshold = (float)object_threshold;
    for (uint32_t y = 0; y < p->height; ++y) {
        for (uint32_t x = 0; x < p->width; ++x) {
            uint32_t index = y * p->width + x;
            float score = p->heat[index];
            if (score < threshold)
                continue;
            uint32_t top = y ? y - 1 : y, bottom = y + 1 < p->height ? y + 1 : y;
            uint32_t left = x ? x - 1 : x, right = x + 1 < p->width ? x + 1 : x;
            int peak = 1;
            for (uint32_t yy = top; yy <= bottom && peak; ++yy)
                for (uint32_t xx = left; xx <= right; ++xx)
                    if (p->heat[yy * p->width + xx] > score) {
                        peak = 0;
                        break;
                    }
            if (!peak)
                continue;
            candidate *c = &p->candidates[count++];
            double w = (double)sigmoid(raw[p->grid + index]) * 512.0;
            double h = (double)sigmoid(raw[2U * p->grid + index]) * 288.0;
            double cx = ((double)x + (double)sigmoid(raw[3U * p->grid + index])) * 4.0;
            double cy = ((double)y + (double)sigmoid(raw[4U * p->grid + index])) * 4.0;
            c->box[0] = cx - w / 2.0;
            c->box[1] = cy - h / 2.0;
            c->box[2] = cx + w / 2.0;
            c->box[3] = cy + h / 2.0;
            c->objectness = score;
            c->index = index;
        }
    }
    qsort(p->candidates, count, sizeof(candidate), ordered);
    candidate *selected[20];
    uint32_t kept = 0, columns = 7U + p->classes;
    for (uint32_t i = 0; i < count && kept < 20; ++i) {
        candidate *c = &p->candidates[i];
        int valid = 1;
        for (uint32_t j = 0; j < kept; ++j)
            if (!(overlap(c, selected[j]) < .35)) {
                valid = 0;
                break;
            }
        if (!valid)
            continue;
        float maximum = raw[5U * p->grid + c->index];
        for (uint32_t j = 1; j < p->classes; ++j) {
            float value = raw[(5U + j) * p->grid + c->index];
            if (value > maximum)
                maximum = value;
        }
        float total = 0.0f;
        for (uint32_t j = 0; j < p->classes; ++j) {
            p->scores[j] = expf(raw[(5U + j) * p->grid + c->index] - maximum);
            total += p->scores[j];
        }
        uint32_t best = 0;
        for (uint32_t j = 0; j < p->classes; ++j) {
            p->scores[j] /= total;
            if (p->scores[j] > p->scores[best])
                best = j;
        }
        double *out = rows + kept * columns;
        for (uint32_t j = 0; j < 4; ++j)
            out[j] = c->box[j];
        out[4] = (double)c->objectness;
        out[5] = (double)p->scores[best];
        out[6] = best;
        for (uint32_t j = 0; j < p->classes; ++j)
            out[7U + j] = p->scores[j];
        selected[kept++] = c;
    }
    *elapsed_ms = clock_ms() - started;
    return (int)kept;
}

#if defined(__GNUC__) && !defined(__clang__)
__attribute__((optimize("fp-contract=off")))
#endif
int native_postprocess_iou_matrix(const double *boxes, uint32_t count, const double *tracks,
                                  uint32_t track_count, double *result) {
#if defined(__clang__)
#pragma clang fp contract(off)
#endif
    if (!count || !track_count)
        return 0;
    if (!boxes || !tracks || !result)
        return -1;
    for (uint64_t i = 0; i < (uint64_t)count * 4U; ++i)
        if (!isfinite(boxes[i]))
            return -3;
    for (uint64_t i = 0; i < (uint64_t)track_count * 4U; ++i)
        if (!isfinite(tracks[i]))
            return -3;
    for (uint32_t i = 0; i < count; ++i) {
        const double *a = boxes + (uint64_t)i * 4U;
        double area_a = fmax(0.0, a[2] - a[0]) * fmax(0.0, a[3] - a[1]);
        for (uint32_t j = 0; j < track_count; ++j) {
            const double *b = tracks + (uint64_t)j * 4U;
            double width = fmax(0.0, fmin(a[2], b[2]) - fmax(a[0], b[0]));
            double height = fmax(0.0, fmin(a[3], b[3]) - fmax(a[1], b[1]));
            double intersection = width * height;
            double area_b = fmax(0.0, b[2] - b[0]) * fmax(0.0, b[3] - b[1]);
            result[(uint64_t)i * track_count + j] =
                intersection / fmax(1e-9, area_a + area_b - intersection);
        }
    }
    return 0;
}
