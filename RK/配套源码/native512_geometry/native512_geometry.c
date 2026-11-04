#include <math.h>
#include <stdint.h>
#include <stdlib.h>

typedef struct {
    double box[4], score;
    uint32_t index;
} candidate;

unsigned native512_geometry_api(void) {
    return 1;
}

static int ordered(const void *left, const void *right) {
    const candidate *a = left, *b = right;
    if (a->score > b->score)
        return -1;
    if (a->score < b->score)
        return 1;
    return a->index < b->index ? -1 : a->index > b->index;
}

static double overlap(const candidate *a, const candidate *b) {
    double width = fmax(0., fmin(a->box[2], b->box[2]) - fmax(a->box[0], b->box[0]));
    double height = fmax(0., fmin(a->box[3], b->box[3]) - fmax(a->box[1], b->box[1]));
    double intersection = width * height;
    double aa = fmax(0., a->box[2] - a->box[0]) * fmax(0., a->box[3] - a->box[1]);
    double ab = fmax(0., b->box[2] - b->box[0]) * fmax(0., b->box[3] - b->box[1]);
    return intersection / fmax(1e-9, aa + ab - intersection);
}

static unsigned select_rows(candidate *rows, unsigned count, unsigned limit, double threshold,
                            uint32_t *indices, double *boxes, double *scores) {
    qsort(rows, count, sizeof(*rows), ordered);
    unsigned selected = 0;
    for (unsigned i = 0; i < count; i++) {
        int accepted = 1;
        for (unsigned j = 0; j < selected; j++) {
            candidate previous;
            for (unsigned k = 0; k < 4; k++)
                previous.box[k] = boxes[j * 4 + k];
            if (!(overlap(&previous, &rows[i]) < threshold)) {
                accepted = 0;
                break;
            }
        }
        if (!accepted)
            continue;
        indices[selected] = rows[i].index;
        for (unsigned k = 0; k < 4; k++)
            boxes[selected * 4 + k] = rows[i].box[k];
        if (scores)
            scores[selected] = rows[i].score;
        if (++selected == limit)
            break;
    }
    return selected;
}

int native512_peaks(const float *p, unsigned height, unsigned width, const uint8_t *mask,
                    float threshold, uint32_t *indices, double *boxes, double *scores) {
    uint64_t grid = (uint64_t)height * width;
    if (!p || !indices || !boxes || !scores || !grid || grid > 1024u * 1024u)
        return -1;
    candidate *rows = malloc(grid * sizeof(*rows));
    if (!rows)
        return -2;
    unsigned count = 0;
    for (unsigned y = 0; y < height; y++)
        for (unsigned x = 0; x < width; x++) {
            unsigned index = y * width + x;
            float value = p[index];
            if (!(value >= threshold) || (mask && !mask[index]))
                continue;
            unsigned top = y ? y - 1 : 0, bottom = y + 1 < height ? y + 1 : height - 1;
            unsigned left = x ? x - 1 : 0, right = x + 1 < width ? x + 1 : width - 1;
            int peak = 1;
            for (unsigned yy = top; yy <= bottom && peak; yy++)
                for (unsigned xx = left; xx <= right; xx++)
                    if (value < p[yy * width + xx]) {
                        peak = 0;
                        break;
                    }
            if (!peak)
                continue;
            candidate *row = &rows[count++];
            row->score = (double)value;
            row->index = index;
            double w = (double)p[grid + index] * 512., h = (double)p[grid * 2 + index] * 288.;
            double cx = ((double)x + (double)p[grid * 3 + index]) * 4.;
            double cy = ((double)y + (double)p[grid * 4 + index]) * 4.;
            row->box[0] = cx - w / 2.;
            row->box[1] = cy - h / 2.;
            row->box[2] = cx + w / 2.;
            row->box[3] = cy + h / 2.;
        }
    unsigned selected = select_rows(rows, count, 20, .35, indices, boxes, scores);
    free(rows);
    return (int)selected;
}

int native512_select(const double *boxes, const double *scores, unsigned count, unsigned limit,
                     double threshold, uint32_t *indices, double *selected_boxes) {
    if (!boxes || !scores || !indices || !selected_boxes || count > 1024u * 1024u)
        return -1;
    if (!count)
        return 0;
    if (!limit || limit > count)
        limit = count;
    candidate *rows = malloc((size_t)count * sizeof(*rows));
    if (!rows)
        return -2;
    for (unsigned i = 0; i < count; i++) {
        rows[i].index = i;
        rows[i].score = scores[i];
        for (unsigned k = 0; k < 4; k++)
            rows[i].box[k] = boxes[i * 4 + k];
    }
    unsigned selected = select_rows(rows, count, limit, threshold, indices, selected_boxes, NULL);
    free(rows);
    return (int)selected;
}

unsigned native512_validate_outputs(const float *p, size_t probabilities, const float *logits,
                                    size_t classes) {
    unsigned nonfinite = 0, range = 0;
    for (size_t i = 0; i < probabilities; i++) {
        float value = p[i];
        nonfinite |= !isfinite(value);
        range |= (value < 0.f || value > 1.f);
    }
    for (size_t i = 0; i < classes; i++)
        nonfinite |= !isfinite(logits[i]);
    return nonfinite ? 1 : range ? 2 : 0;
}
