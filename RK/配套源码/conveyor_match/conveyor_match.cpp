#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <vector>

extern "C" int conveyor_match_api_version() {
    return 1;
}

extern "C" int conveyor_match_assign(const double *costs, const double *positions,
                                     const double *orders, int n, int m, double unmatched,
                                     int *matched, double *margins) {
    if (n < 0 || m < 0 || n > 256 || m > 256 || !std::isfinite(unmatched) || unmatched <= 0)
        return -1;
    std::fill(matched, matched + n, -1);
    std::fill(margins, margins + n, 0.);
    if (!n || !m)
        return 0;
    std::vector<int> rows(n), cols(m);
    std::iota(rows.begin(), rows.end(), 0);
    std::iota(cols.begin(), cols.end(), 0);
    std::stable_sort(rows.begin(), rows.end(),
                     [&](int a, int b) { return positions[a] < positions[b]; });
    std::stable_sort(cols.begin(), cols.end(), [&](int a, int b) { return orders[a] < orders[b]; });
    const int stride = m + 1;
    const double infinity = std::numeric_limits<double>::infinity(), skip = unmatched * .51;
    std::vector<double> dp((n + 1) * stride, infinity), suffix((n + 1) * stride, infinity);
    std::vector<unsigned char> back((n + 1) * stride, 0);
    dp[0] = 0.;
    for (int i = 0; i <= n; ++i)
        for (int j = 0; j <= m; ++j) {
            const double value = dp[i * stride + j];
            if (i < n && value + skip < dp[(i + 1) * stride + j]) {
                dp[(i + 1) * stride + j] = value + skip;
                back[(i + 1) * stride + j] = 1;
            }
            if (j < m && value + skip < dp[i * stride + j + 1]) {
                dp[i * stride + j + 1] = value + skip;
                back[i * stride + j + 1] = 2;
            }
            if (i < n && j < m) {
                const double cost = costs[rows[i] * m + cols[j]];
                if (cost < unmatched && value + cost < dp[(i + 1) * stride + j + 1]) {
                    dp[(i + 1) * stride + j + 1] = value + cost;
                    back[(i + 1) * stride + j + 1] = 3;
                }
            }
        }
    int i = n, j = m;
    while (i || j) {
        const auto mode = back[i * stride + j];
        if (mode == 1)
            --i;
        else if (mode == 2)
            --j;
        else if (mode == 3) {
            matched[rows[i - 1]] = cols[j - 1];
            --i;
            --j;
        } else
            return -2;
    }
    suffix[n * stride + m] = 0.;
    for (int a = n; a >= 0; --a)
        for (int b = m; b >= 0; --b) {
            double &value = suffix[a * stride + b];
            if (a < n)
                value = std::min(value, skip + suffix[(a + 1) * stride + b]);
            if (b < m)
                value = std::min(value, skip + suffix[a * stride + b + 1]);
            if (a < n && b < m) {
                const double cost = costs[rows[a] * m + cols[b]];
                if (cost < unmatched)
                    value = std::min(value, cost + suffix[(a + 1) * stride + b + 1]);
            }
        }
    const double best = dp[n * stride + m];
    for (int a = 0; a < n; ++a) {
        const int row = rows[a], owner = matched[row];
        if (owner < 0)
            continue;
        double alternative = infinity;
        for (int b = 0; b <= m; ++b)
            alternative =
                std::min(alternative, dp[a * stride + b] + skip + suffix[(a + 1) * stride + b]);
        for (int b = 0; b < m; ++b) {
            const int col = cols[b];
            const double cost = costs[row * m + col];
            if (col != owner && cost < unmatched)
                alternative = std::min(alternative, dp[a * stride + b] + cost +
                                                        suffix[(a + 1) * stride + b + 1]);
        }
        margins[row] = std::max(0., alternative - best);
    }
    return 0;
}
