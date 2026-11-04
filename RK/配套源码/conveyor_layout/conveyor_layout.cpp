#include <algorithm>
#include <cmath>
#include <vector>

namespace {
struct Match {
    double quality;
    int pair;
};
struct Hypothesis {
    double score = 0, dx = 0, dy = 0, width = 0;
    std::vector<Match> chosen;
};
double median(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    const auto n = values.size();
    return n % 2 ? values[n / 2] : (values[n / 2 - 1] + values[n / 2]) * .5;
}
}

extern "C" int conveyor_layout_api_version() {
    return 1;
}

extern "C" int conveyor_layout_select(const double *pairs, int count, int old_count, int new_count,
                                      double *summary, int *selected, double *qualities,
                                      int *ambiguous) {
    if (!pairs || !summary || !selected || !qualities || !ambiguous || count < 1 || count > 144 ||
        old_count < 1 || old_count > 144 || new_count < 1 || new_count > 144)
        return -1;
    for (int k = 0; k < count; ++k) {
        const auto p = pairs + 7 * k;
        for (int j = 0; j < 7; ++j)
            if (!std::isfinite(p[j]))
                return -2;
        if (p[0] != std::floor(p[0]) || p[1] != std::floor(p[1]) || p[0] < 0 || p[0] >= old_count ||
            p[1] < 0 || p[1] >= new_count || p[4] <= 0 || p[5] <= 0)
            return -3;
    }
    std::fill(ambiguous, ambiguous + new_count, 0);
    std::vector<Hypothesis> hypotheses;
    hypotheses.reserve(count);
    int best_index = 0;
    for (int seed = 0; seed < count; ++seed) {
        std::vector<Match> matches;
        const auto a = pairs + 7 * seed;
        for (int k = 0; k < count; ++k) {
            const auto p = pairs + 7 * k;
            const double residual = std::abs(a[2] - p[2]) / std::max(10., p[4] * .2) +
                                    std::abs(a[3] - p[3]) / std::max(7., p[5] * .2);
            if (residual < 2.)
                matches.push_back({p[6] - .12 * residual, k});
        }

        std::sort(matches.begin(), matches.end(), [pairs](const Match &a, const Match &b) {
            if (a.quality != b.quality)
                return a.quality > b.quality;
            for (int j = 0; j < 7; ++j) {
                const auto x = pairs[7 * a.pair + j], y = pairs[7 * b.pair + j];
                if (x != y)
                    return x > y;
            }
            return false;
        });
        std::vector<bool> used_old(old_count, false), used_new(new_count, false);
        Hypothesis h;
        std::vector<double> dx, dy, width;
        for (const auto &match : matches) {
            const auto p = pairs + 7 * match.pair;
            const auto i = static_cast<int>(p[0]), j = static_cast<int>(p[1]);
            if (used_old[i] || used_new[j])
                continue;
            used_old[i] = true;
            used_new[j] = true;
            h.chosen.push_back(match);
            h.score += .3 + .7 * std::max(0., match.quality);
            dx.push_back(p[2]);
            dy.push_back(p[3]);
            width.push_back(p[4]);
        }
        if (h.chosen.empty())
            return -4;
        h.dx = median(dx);
        h.dy = median(dy);
        h.width = median(width);
        hypotheses.push_back(h);
        if (h.score > hypotheses[best_index].score)
            best_index = seed;
    }
    const auto &best = hypotheses[best_index];
    const double separation = std::max(12., best.width * .4);
    double runner = 0;
    for (const auto &h : hypotheses)
        if (std::abs(h.dx - best.dx) > separation)
            runner = std::max(runner, h.score);
    const int minimum = std::min(old_count, new_count) == 1 ? 1 : 2;
    const double required_margin =
        minimum == 1 ? std::max(.07, .08 * best.score) : std::max(.2, .1 * best.score);
    std::vector<int> owners(new_count, -1);
    for (const auto &match : best.chosen)
        owners[static_cast<int>(pairs[7 * match.pair + 1])] =
            static_cast<int>(pairs[7 * match.pair]);
    for (const auto &h : hypotheses) {
        if (h.score < best.score - required_margin || std::abs(h.dx - best.dx) <= separation)
            continue;
        std::vector<int> other(new_count, -1);
        for (const auto &match : h.chosen)
            other[static_cast<int>(pairs[7 * match.pair + 1])] =
                static_cast<int>(pairs[7 * match.pair]);
        for (int j = 0; j < new_count; ++j)
            if (owners[j] != other[j])
                ambiguous[j] = 1;
    }
    summary[0] = best.score;
    summary[1] = runner;
    summary[2] = best.dx;
    summary[3] = best.dy;
    summary[4] = best.width;
    summary[5] = required_margin;
    for (unsigned k = 0; k < best.chosen.size(); ++k) {
        selected[k] = best.chosen[k].pair;
        qualities[k] = best.chosen[k].quality;
    }
    return static_cast<int>(best.chosen.size());
}
