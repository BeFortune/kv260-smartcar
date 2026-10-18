#include "ffn_accel.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
const std::size_t D = ffn_config::input_dim;
const std::size_t H = ffn_config::hidden_dim;
const std::size_t GUARD = 16;
const float SENTINEL = 12345.25f;

struct Weights {
    std::vector<float> w1, b1, w2, b2;
    Weights() : w1(D * H), b1(H), w2(H * D), b2(D) {}
};

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void fill_random(std::vector<float>& values, std::mt19937& rng,
                 float magnitude) {
    std::uniform_real_distribution<float> distribution(-magnitude, magnitude);
    for (float& value : values) {
        value = distribution(rng);
    }
}

Weights random_weights(std::uint32_t seed) {
    std::mt19937 rng(seed);
    Weights weights;
    fill_random(weights.w1, rng, 0.20f);
    fill_random(weights.b1, rng, 0.50f);
    fill_random(weights.w2, rng, 0.15f);
    fill_random(weights.b2, rng, 0.30f);
    return weights;
}

// Deliberately different loop ordering from the kernel, and double arithmetic
// throughout the two reductions and exact erf GELU. Inputs remain float32.
std::vector<double> reference(const std::vector<float>& x,
                              const Weights& weights, unsigned int count) {
    std::vector<double> y(static_cast<std::size_t>(count) * D);
    const double sqrt_two = std::sqrt(2.0);
    for (unsigned int t = 0; t < count; ++t) {
        std::vector<double> hidden(H, 0.0);
        for (std::size_t i = 0; i < D; ++i) {
            const double input = static_cast<double>(x[t * D + i]);
            for (std::size_t j = 0; j < H; ++j) {
                hidden[j] += input * static_cast<double>(weights.w1[i * H + j]);
            }
        }
        for (std::size_t j = 0; j < H; ++j) {
            const double z = hidden[j] + static_cast<double>(weights.b1[j]);
            hidden[j] = 0.5 * z * (1.0 + std::erf(z / sqrt_two));
        }
        for (std::size_t j = 0; j < H; ++j) {
            for (std::size_t k = 0; k < D; ++k) {
                y[t * D + k] += hidden[j] *
                    static_cast<double>(weights.w2[j * D + k]);
            }
        }
        for (std::size_t k = 0; k < D; ++k) {
            y[t * D + k] += static_cast<double>(weights.b2[k]);
        }
    }
    return y;
}

void run_case(const std::string& name, unsigned int count,
              const std::vector<float>& x, const Weights& weights) {
    const std::size_t size = static_cast<std::size_t>(count) * D;
    // RTL co-simulation marshals the declared m_axi depth, not count * D.
    const std::size_t capacity = ffn_config::max_tokens * D;
    std::vector<float> guarded(capacity + 2 * GUARD, SENTINEL);
    float* y = guarded.data() + GUARD;
    ffn_accel(x.data(), y, weights.w1.data(), weights.b1.data(),
              weights.w2.data(), weights.b2.data(), count);
    const std::vector<double> expected = reference(x, weights, count);
    double max_abs_error = 0.0;
    double tail_abs_error = 0.0;
    // Float reductions are not bit-identical to double or PS BLAS reductions.
    const double atol = 2.0e-5;
    const double rtol = 2.0e-5;
    for (std::size_t i = 0; i < size; ++i) {
        const double error = std::abs(static_cast<double>(y[i]) - expected[i]);
        require(std::isfinite(y[i]) && error <= atol + rtol * std::abs(expected[i]),
                name + ": mismatch at element " + std::to_string(i));
        max_abs_error = std::max(max_abs_error, error);
        if (i >= size - D) {
            tail_abs_error = std::max(tail_abs_error, error);
        }
    }
    for (std::size_t i = 0; i < GUARD; ++i) {
        require(guarded[i] == SENTINEL && guarded[GUARD + capacity + i] == SENTINEL,
                name + ": output guard overwritten");
    }
    require(std::all_of(guarded.begin() + GUARD + size,
                        guarded.begin() + GUARD + capacity,
                        [](float value) { return value == SENTINEL; }),
            name + ": inactive output tokens overwritten");
    std::cout << "PASS " << name << " tokens=" << count
              << " max_abs=" << max_abs_error
              << " tail_abs=" << tail_abs_error << '\n';
}

void test_invalid(const Weights& weights) {
    const unsigned int counts[] = {
        0, ffn_config::max_tokens + 1, std::numeric_limits<unsigned int>::max()
    };
    for (unsigned int count : counts) {
        std::vector<float> output(ffn_config::max_tokens * D + 2 * GUARD, SENTINEL);
        std::vector<float> x(ffn_config::max_tokens * D, 1.0f);
        ffn_accel(x.data(), output.data() + GUARD, weights.w1.data(),
                  weights.b1.data(), weights.w2.data(), weights.b2.data(), count);
        require(std::all_of(output.begin(), output.end(),
                            [](float value) { return value == SENTINEL; }),
                "invalid token count modified output");
#if !defined(FFN_USE_HLS_MATH) && !defined(__SYNTHESIS__)
        // Host-only: RTL cosim adapters read pointer buffers before invocation.
        ffn_accel(nullptr, output.data() + GUARD, nullptr, nullptr, nullptr,
                  nullptr, count);
        ffn_accel(nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, count);
        std::cout << "PASS invalid tokens=" << count << " no buffer accesses\n";
#else
        std::cout << "PASS invalid tokens=" << count << " output untouched\n";
#endif
    }
}
}

int main() {
    try {
        std::vector<float> x(ffn_config::max_tokens * D);
        std::mt19937 rng(20261014);
        fill_random(x, rng, 1.0f);
        // Make the last token distinct; every tail output is compared above.
        for (std::size_t i = 0; i < D; ++i) {
            x[(ffn_config::max_tokens - 1) * D + i] =
                (static_cast<int>(i % 11) - 5) * 0.13f;
        }
        const Weights first = random_weights(11);
        const unsigned int small_counts[] = {1, 2, 3, 4, 5, 7, 8, 9};
        for (unsigned int count : small_counts) {
            run_case("small/boundary", count, x, first);
        }
        run_case("near-maximum", 659, x, first);
        run_case("camera-A", 660, x, first);
        run_case("camera-B/reload", 660, x, random_weights(22));
        run_case("cross-A/reload", 4, x, random_weights(33));
        run_case("cross-B/reload", 4, x, random_weights(44));
        run_case("camera-A/revisit", 1, x, first);

        Weights zero;
        run_case("zero", 4, x, zero);
        Weights bias_only;
        for (std::size_t j = 0; j < H; ++j) {
            bias_only.b1[j] = (static_cast<int>(j % 17) - 8) * 0.75f;
            for (std::size_t k = 0; k < D; ++k) {
                bias_only.w2[j * D + k] =
                    (static_cast<int>((j + 3 * k) % 13) - 6) * 0.01f;
            }
        }
        for (std::size_t k = 0; k < D; ++k) {
            bias_only.b2[k] = (static_cast<int>(k % 9) - 4) * 0.025f;
        }
        run_case("bias/exact-erf", 4, x, bias_only);
        test_invalid(first);
        std::cout << "All FFN reference tests passed.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
