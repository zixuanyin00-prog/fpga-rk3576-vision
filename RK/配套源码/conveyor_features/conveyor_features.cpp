#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
#include <algorithm>
#include <cmath>
#include "patch_stats.cpp"

extern "C" int conveyor_features_api_version() {
    cv::setNumThreads(1);
    return 1;
}
extern "C" const char *conveyor_features_opencv_version() {
    return CV_VERSION;
}

extern "C" int conveyor_features_extract(const unsigned char *pixels, int width, int height,
                                         const double *boxes, int count, double origin_x,
                                         double origin_y, int mode, unsigned char *valid,
                                         float *statistics, float *vectors) {
    if (!pixels || width <= 0 || height <= 0 || count < 0 || count > 256 || mode < 0 || mode > 2)
        return -1;
    cv::Mat gray(height, width, CV_8UC1, const_cast<unsigned char *>(pixels));
    const double inset = mode == 0 ? 0. : mode == 1 ? .12 : .1;
    for (int i = 0; i < count; ++i) {
        const double *box = boxes + 4 * i;
        const double mx = (box[2] - box[0]) * inset, my = (box[3] - box[1]) * inset;

        int left = std::max(0, static_cast<int>(box[0] - origin_x + mx));
        int right = std::min(width, static_cast<int>(box[2] - origin_x - mx));
        int top = std::max(0, static_cast<int>(box[1] - origin_y + my));
        int bottom = std::min(height, static_cast<int>(box[3] - origin_y - my));
        if (mode == 1 && (right - left < 5 || bottom - top < 5)) {
            valid[i] = 0;
            continue;
        }
        left = std::min(left, width);
        top = std::min(top, height);
        if (right < 0)
            right = std::max(0, width + right);
        if (bottom < 0)
            bottom = std::max(0, height + bottom);
        valid[i] = 0;
        const int minimum = mode == 0 ? 10 : 5;
        if (right - left < minimum || bottom - top < minimum)
            continue;
        cv::Mat resized, patch;
        cv::resize(gray(cv::Rect(left, top, right - left, bottom - top)), resized,
                   mode == 0 ? cv::Size(40, 28) : cv::Size(24, 16), 0., 0., cv::INTER_AREA);
        resized.convertTo(patch, CV_32FC1);
        if (mode == 0)
            passage_face_edges(patch.ptr<float>(), statistics + 4 * i);
        else
            passage_patch_stats(patch.ptr<float>(), 384, vectors + 384 * i, statistics + 4 * i);
        valid[i] = 1;
    }
    return 0;
}
