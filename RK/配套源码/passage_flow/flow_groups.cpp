#include <algorithm>
#include <vector>
#include <cmath>

static float middle(std::vector<float> &values) {
    std::sort(values.begin(), values.end());
    auto n = values.size();
    return n % 2 ? values[n / 2] : (values[n / 2 - 1] + values[n / 2]) / 2.f;
}

extern "C" int passage_flow_select_groups(const float *a, const float *b, const float *back,
                                          const unsigned char *status,
                                          const unsigned char *back_status, int point_count,
                                          const int *boundaries, int group_count, float *shifts,
                                          unsigned char *selected, int *support) {
    if (!a || !b || !back || !status || !back_status || !boundaries || !shifts || !selected ||
        !support)
        return -1;
    std::fill(selected, selected + point_count, 0);
    try {
        for (int group = 0; group < group_count; ++group) {
            support[group] = 0;
            int start = boundaries[2 * group], end = boundaries[2 * group + 1];
            if (start < 0 || end < start || end > point_count)
                return -2;
            std::vector<int> valid;
            std::vector<float> xs, ys;
            for (int i = start; i < end; ++i) {
                float dx = back[2 * i] - a[2 * i], dy = back[2 * i + 1] - a[2 * i + 1];
                if (!status[i] || !back_status[i] || !(std::sqrt(dx * dx + dy * dy) < 1.8f))
                    continue;
                valid.push_back(i);
                xs.push_back(b[2 * i] - a[2 * i]);
                ys.push_back(b[2 * i + 1] - a[2 * i + 1]);
            }
            if (valid.size() < 3)
                continue;
            float x = middle(xs), y = middle(ys);
            float radius = static_cast<float>(
                std::max(2., static_cast<double>(std::sqrt(x * x + y * y)) * .12));
            xs.clear();
            ys.clear();
            std::vector<int> coherent;
            for (int i : valid) {
                float vx = b[2 * i] - a[2 * i], vy = b[2 * i + 1] - a[2 * i + 1];
                float dx = vx - x, dy = vy - y;
                if (!(std::sqrt(dx * dx + dy * dy) < radius))
                    continue;
                coherent.push_back(i);
                xs.push_back(vx);
                ys.push_back(vy);
            }
            if (coherent.size() < 3)
                continue;
            shifts[2 * group] = middle(xs);
            shifts[2 * group + 1] = middle(ys);
            support[group] = static_cast<int>(coherent.size());
            for (int i : coherent)
                selected[i] = 1;
        }
        return 0;
    } catch (...) {
        return -3;
    }
}
