#include <opencv2/core.hpp>
#include <opencv2/video/tracking.hpp>
#include <vector>
#include <cstdint>
#include <new>
#include "native_parallel.inc"

struct FlowCache {
    std::vector<cv::Mat> previous_pyramid;
    std::uintptr_t previous_token = 0;
    int width = 0, height = 0;
};
extern "C" {
int passage_flow_api_version() {
    return 1;
}
const char *passage_flow_opencv_version() {
    return CV_VERSION;
}
void *passage_flow_create() {
    try {
        if (!pinned_flow_backend && prepare_flow_plan(1))
            return nullptr;
        return new FlowCache;
    } catch (...) {
        return nullptr;
    }
}
void passage_flow_destroy(void *pointer) {
    delete static_cast<FlowCache *>(pointer);
}
int passage_flow_fb(void *pointer, const unsigned char *previous, const unsigned char *current,
                    int width, int height, std::uintptr_t previous_token,
                    std::uintptr_t current_token, const float *points, int count, float *forward,
                    float *backward, unsigned char *forward_status,
                    unsigned char *backward_status) {
    if (!pointer || !previous || !current || !points || count < 1 || width < 1 || height < 1)
        return -1;
    try {
        auto &cache = *static_cast<FlowCache *>(pointer);
        cv::Mat before(height, width, CV_8UC1, const_cast<unsigned char *>(previous));
        cv::Mat after(height, width, CV_8UC1, const_cast<unsigned char *>(current));

        if (cache.previous_token != previous_token || cache.width != width ||
            cache.height != height)
            cv::buildOpticalFlowPyramid(before, cache.previous_pyramid, {25, 25}, 3);
        std::vector<cv::Mat> current_pyramid;
        cv::buildOpticalFlowPyramid(after, current_pyramid, {25, 25}, 3);
        cv::Mat a(count, 1, CV_32FC2, const_cast<float *>(points));
        cv::Mat b(count, 1, CV_32FC2, forward), back(count, 1, CV_32FC2, backward);
        cv::Mat ok(count, 1, CV_8UC1, forward_status), back_ok(count, 1, CV_8UC1, backward_status);
        cv::Mat errors, back_errors;
        auto criteria = cv::TermCriteria(cv::TermCriteria::EPS | cv::TermCriteria::COUNT, 15, .03);
        cv::calcOpticalFlowPyrLK(cache.previous_pyramid, current_pyramid, a, b, ok, errors,
                                 {25, 25}, 3, criteria);
        cv::calcOpticalFlowPyrLK(current_pyramid, cache.previous_pyramid, b, back, back_ok,
                                 back_errors, {25, 25}, 3, criteria);
        cache.previous_pyramid = std::move(current_pyramid);
        cache.previous_token = current_token;
        cache.width = width;
        cache.height = height;
        return 0;
    } catch (...) {
        return -2;
    }
}
}
