#define _POSIX_C_SOURCE 200809L
#include "rknn_api.h"
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
    uint32_t index, n_dims, dims[16], fmt, type, qnt_type;
    int32_t zp, fl;
    float scale;
    uint32_t n_elems, size, size_with_stride, w_stride, h_stride;
    char name[256];
} native_tensor_info;

typedef struct {
    double inputs_set_ms, run_ms, outputs_get_ms, convert_ms, release_ms, total_ms;
    uint64_t sequence;
} native_timing;

typedef struct {
    rknn_context context;
    int initialized, collect_perf;
    rknn_input_output_num count;
    rknn_tensor_attr input;
    uint32_t input_width, input_height, input_bytes;
    rknn_tensor_attr *attrs;
    rknn_output *outputs;
    float **buffers, **nchw;
    native_timing timing;
    char error[512];
} native_engine;

static double now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec * 1000.0 + (double)t.tv_nsec / 1000000.0;
}

static int fail(native_engine *e, const char *stage, int code) {
    if (e)
        snprintf(e->error, sizeof(e->error), "%s failed (%d)", stage, code);
    return code ? code : -1;
}

void native_engine_close(void *handle) {
    native_engine *e = handle;
    if (!e)
        return;
    if (e->initialized)
        rknn_destroy(e->context);
    for (uint32_t i = 0; i < e->count.n_output; ++i) {
        if (e->buffers)
            free(e->buffers[i]);
        if (e->nchw)
            free(e->nchw[i]);
    }
    free(e->buffers);
    free(e->nchw);
    free(e->outputs);
    free(e->attrs);
    free(e);
}

static int prepare(native_engine *e, const char *path, uint32_t core_mask, int collect_perf) {
    FILE *f = fopen(path, "rb");
    if (!f)
        return fail(e, "open model", -errno);
    if (fseek(f, 0, SEEK_END)) {
        fclose(f);
        return fail(e, "model seek", -1);
    }
    long length = ftell(f);
    if (length <= 0 || (uint64_t)length > UINT32_MAX) {
        fclose(f);
        return fail(e, "model size", -1);
    }
    rewind(f);
    void *model = malloc((size_t)length);
    if (!model) {
        fclose(f);
        return fail(e, "model allocation", -1);
    }
    size_t read_length = fread(model, 1, (size_t)length, f);
    fclose(f);
    if (read_length != (size_t)length) {
        free(model);
        return fail(e, "read model", -1);
    }
    uint32_t flags = collect_perf ? RKNN_FLAG_COLLECT_PERF_MASK : 0;
    int rc = rknn_init(&e->context, model, (uint32_t)length, flags, NULL);
    free(model);
    if (rc)
        return fail(e, "rknn_init", rc);
    e->initialized = 1;
    e->collect_perf = collect_perf;
    rc = rknn_set_core_mask(e->context, (rknn_core_mask)core_mask);
    if (rc)
        return fail(e, "rknn_set_core_mask", rc);
    rc = rknn_query(e->context, RKNN_QUERY_IN_OUT_NUM, &e->count, sizeof(e->count));
    if (rc)
        return fail(e, "query input/output count", rc);
    if (e->count.n_input != 1 || !e->count.n_output || e->count.n_output > 32)
        return fail(e, "unsupported input/output count", -1);
    e->input.index = 0;
    rc = rknn_query(e->context, RKNN_QUERY_INPUT_ATTR, &e->input, sizeof(e->input));
    if (rc)
        return fail(e, "query input", rc);
    if (e->input.n_dims != 4 || e->input.dims[0] != 1)
        return fail(e, "requires one static batch-1 RGB input", -1);
    if (e->input.fmt == RKNN_TENSOR_NCHW && e->input.dims[1] == 3) {
        e->input_height = e->input.dims[2];
        e->input_width = e->input.dims[3];
    } else if (e->input.fmt == RKNN_TENSOR_NHWC && e->input.dims[3] == 3) {
        e->input_height = e->input.dims[1];
        e->input_width = e->input.dims[2];
    } else
        return fail(e, "input must have static RGB NCHW or NHWC shape", -1);
    uint64_t pixels = (uint64_t)e->input_width * e->input_height;
    if (!pixels || pixels > UINT32_MAX / 3U || e->input.n_elems != pixels * 3U)
        return fail(e, "invalid static RGB input dimensions", -1);
    e->input_bytes = (uint32_t)pixels * 3U;
    uint32_t n = e->count.n_output;
    e->attrs = calloc(n, sizeof(*e->attrs));
    e->outputs = calloc(n, sizeof(*e->outputs));
    e->buffers = calloc(n, sizeof(*e->buffers));
    e->nchw = calloc(n, sizeof(*e->nchw));
    if (!e->attrs || !e->outputs || !e->buffers || !e->nchw)
        return fail(e, "output allocation", -1);
    for (uint32_t i = 0; i < n; ++i) {
        rknn_tensor_attr *a = &e->attrs[i];
        a->index = i;
        rc = rknn_query(e->context, RKNN_QUERY_OUTPUT_ATTR, a, sizeof(*a));
        if (rc)
            return fail(e, "query output", rc);
        if (a->n_dims != 4 || (a->fmt != RKNN_TENSOR_NCHW && a->fmt != RKNN_TENSOR_NHWC) ||
            !a->n_elems || a->n_elems > UINT32_MAX / sizeof(float))
            return fail(e, "unsupported output shape/layout", -1);
        e->buffers[i] = malloc((size_t)a->n_elems * sizeof(float));
        if (!e->buffers[i])
            return fail(e, "float output allocation", -1);
        if (a->fmt == RKNN_TENSOR_NHWC) {
            e->nchw[i] = malloc((size_t)a->n_elems * sizeof(float));
            if (!e->nchw[i])
                return fail(e, "NCHW output allocation", -1);
        }
    }
    e->error[0] = '\0';
    return 0;
}

void *native_engine_open(const char *path, uint32_t core_mask, int collect_perf, char *error,
                         uint32_t capacity) {
    native_engine *e = calloc(1, sizeof(*e));
    if (!e) {
        if (error && capacity)
            snprintf(error, capacity, "engine allocation failed");
        return NULL;
    }
    int rc = prepare(e, path, core_mask, collect_perf);
    if (rc) {
        if (error && capacity)
            snprintf(error, capacity, "%s", e->error);
        native_engine_close(e);
        return NULL;
    }
    return e;
}

const char *native_engine_error(void *handle) {
    native_engine *e = handle;
    return e ? e->error : "engine is closed";
}

int native_engine_query(void *handle, uint32_t which, uint32_t index, native_tensor_info *info) {
    native_engine *e = handle;
    if (!e || !info)
        return -1;
    rknn_query_cmd cmd;
    if (which == 0 && index == 0)
        cmd = RKNN_QUERY_INPUT_ATTR;
    else if (which == 1 && index < e->count.n_output)
        cmd = RKNN_QUERY_OUTPUT_ATTR;
    else if (which == 2 && index == 0)
        cmd = RKNN_QUERY_NATIVE_INPUT_ATTR;
    else if (which == 3 && index < e->count.n_output)
        cmd = RKNN_QUERY_NATIVE_OUTPUT_ATTR;
    else
        return fail(e, "invalid tensor query", -1);
    rknn_tensor_attr a = {0};
    a.index = index;
    int rc = rknn_query(e->context, cmd, &a, sizeof(a));
    if (rc)
        return fail(e, "query tensor attributes", rc);
    memset(info, 0, sizeof(*info));
    info->index = a.index;
    info->n_dims = a.n_dims;
    memcpy(info->dims, a.dims, sizeof(info->dims));
    memcpy(info->name, a.name, sizeof(info->name));
    info->name[255] = '\0';
    info->fmt = a.fmt;
    info->type = a.type;
    info->qnt_type = a.qnt_type;
    info->zp = a.zp;
    info->fl = a.fl;
    info->scale = a.scale;
    info->n_elems = a.n_elems;
    info->size = a.size;
    info->size_with_stride = a.size_with_stride;
    info->w_stride = a.w_stride;
    info->h_stride = a.h_stride;
    return 0;
}

uint32_t native_engine_output_count(void *handle) {
    native_engine *e = handle;
    return e ? e->count.n_output : 0;
}

void *native_engine_output(void *handle, uint32_t index) {
    native_engine *e = handle;
    if (!e || index >= e->count.n_output)
        return NULL;
    return e->nchw[index] ? e->nchw[index] : e->buffers[index];
}

int native_engine_infer(void *handle, const uint8_t *rgb, uint32_t bytes) {
    native_engine *e = handle;
    if (!e || !rgb)
        return -1;
    if (bytes != e->input_bytes)
        return fail(e, "RGB input size mismatch", -1);
    native_timing t = {0};
    t.sequence = e->timing.sequence + 1;
    rknn_input input = {0};
    input.index = 0;
    input.buf = (void *)rgb;
    input.size = bytes;
    input.type = RKNN_TENSOR_UINT8;
    input.fmt = RKNN_TENSOR_NHWC;
    input.pass_through = 0;
    double started = now_ms(), stage = started;
    int rc = rknn_inputs_set(e->context, 1, &input);
    t.inputs_set_ms = now_ms() - stage;
    if (rc)
        return fail(e, "rknn_inputs_set", rc);
    stage = now_ms();
    rc = rknn_run(e->context, NULL);
    t.run_ms = now_ms() - stage;
    if (rc)
        return fail(e, "rknn_run", rc);
    for (uint32_t i = 0; i < e->count.n_output; ++i) {
        e->outputs[i].index = i;
        e->outputs[i].want_float = 1;
        e->outputs[i].is_prealloc = 1;
        e->outputs[i].buf = e->buffers[i];
        e->outputs[i].size = e->attrs[i].n_elems * sizeof(float);
    }
    stage = now_ms();
    rc = rknn_outputs_get(e->context, e->count.n_output, e->outputs, NULL);
    t.outputs_get_ms = now_ms() - stage;
    if (rc)
        return fail(e, "rknn_outputs_get", rc);
    stage = now_ms();
    for (uint32_t i = 0; i < e->count.n_output; ++i) {
        if (!e->nchw[i])
            continue;
        const rknn_tensor_attr *a = &e->attrs[i];
        uint32_t batch = a->dims[0], h = a->dims[1], w = a->dims[2], channels = a->dims[3];
        for (uint32_t b = 0; b < batch; ++b)
            for (uint32_t c = 0; c < channels; ++c)
                for (uint32_t y = 0; y < h; ++y)
                    for (uint32_t x = 0; x < w; ++x)
                        e->nchw[i][((b * channels + c) * h + y) * w + x] =
                            e->buffers[i][((b * h + y) * w + x) * channels + c];
    }
    t.convert_ms = now_ms() - stage;
    stage = now_ms();
    rc = rknn_outputs_release(e->context, e->count.n_output, e->outputs);
    t.release_ms = now_ms() - stage;
    t.total_ms = now_ms() - started;
    e->timing = t;
    if (rc)
        return fail(e, "rknn_outputs_release", rc);
    e->error[0] = '\0';
    return 0;
}

int native_engine_timing(void *handle, native_timing *timing) {
    native_engine *e = handle;
    if (!e || !timing)
        return -1;
    *timing = e->timing;
    return 0;
}

int native_engine_infer_timed(void *handle, const uint8_t *rgb, uint32_t bytes,
                              native_timing *timing) {
    if (!timing)
        return -1;
    int rc = native_engine_infer(handle, rgb, bytes);
    if (rc)
        return rc;
    return native_engine_timing(handle, timing);
}

int native_engine_perf_run(void *handle, int64_t *microseconds) {
    native_engine *e = handle;
    if (!e || !microseconds)
        return -1;
    rknn_perf_run p = {0};
    int rc = rknn_query(e->context, RKNN_QUERY_PERF_RUN, &p, sizeof(p));
    if (rc)
        return fail(e, "query perf run", rc);
    *microseconds = p.run_duration;
    return 0;
}

int native_engine_perf_detail(void *handle, const char **text, uint64_t *length) {
    native_engine *e = handle;
    if (!e || !text || !length)
        return -1;
    if (!e->collect_perf)
        return fail(e, "perf detail requires collect_perf at init", -1);
    rknn_perf_detail p = {0};
    int rc = rknn_query(e->context, RKNN_QUERY_PERF_DETAIL, &p, sizeof(p));
    if (rc)
        return fail(e, "query perf detail", rc);
    *text = p.perf_data;
    *length = p.data_len;
    return 0;
}

int native_engine_version(void *handle, char *api, char *driver, uint32_t capacity) {
    native_engine *e = handle;
    if (!e || !api || !driver || !capacity)
        return -1;
    rknn_sdk_version version = {0};
    int rc = rknn_query(e->context, RKNN_QUERY_SDK_VERSION, &version, sizeof(version));
    if (rc)
        return fail(e, "query SDK version", rc);
    snprintf(api, capacity, "%s", version.api_version);
    snprintf(driver, capacity, "%s", version.drv_version);
    return 0;
}
