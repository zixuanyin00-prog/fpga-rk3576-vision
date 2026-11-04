#include <cmath>
#include <algorithm>
extern "C" int passage_patch_stats(const float *patch, int count, float *vector, float *stats) {
    if (!patch || !vector || !stats || count != 384)
        return -1;
    float sum = 0.f;
    for (int i = 0; i < count; ++i)
        sum += patch[i];
    float mean = sum / static_cast<float>(count), square = 0.f;
    for (int i = 0; i < count; ++i) {
        float value = patch[i] - mean;
        vector[i] = value;
        square += value * value;
    }
    float norm = std::sqrt(square), denominator = std::max(norm, 1e-6f);
    for (int i = 0; i < count; ++i)
        vector[i] /= denominator;
    stats[0] = mean;
    stats[1] = std::sqrt(square / static_cast<float>(count));
    stats[2] = norm;
    return 0;
}
extern "C" int passage_face_edges(const float *image, float *result) {
    if (!image || !result)
        return -1;
    float xp[39], maximum_x = 0.f, maximum_y = 0.f;
    for (int x = 0; x < 39; ++x) {
        float sum = 0.f;
        for (int y = 5; y < 23; ++y)
            sum += std::abs(image[y * 40 + x + 1] - image[y * 40 + x]);
        xp[x] = sum / 18.f;
        maximum_x = std::max(maximum_x, xp[x]);
    }
    for (int y = 0; y < 27; ++y) {
        float sum = 0.f;
        for (int x = 5; x < 35; ++x)
            sum += std::abs(image[(y + 1) * 40 + x] - image[y * 40 + x]);
        maximum_y = std::max(maximum_y, sum / 30.f);
    }
    float left = 0.f, right = 0.f;
    for (int i = 0; i < 10; ++i) {
        left += xp[i];
        right += xp[29 + i];
    }
    result[0] = maximum_y;
    result[1] = maximum_x;
    result[2] = left / 10.f;
    result[3] = right / 10.f;
    return 0;
}
