#include "test_support.hpp"

int main() {
    try {
        std::vector<float> x(ffn_config::max_tokens * D);
        std::mt19937 rng(20261014);
        fill_random(x, rng, 1.0f);
        for (std::size_t i = 0; i < D; ++i) {
            x[(ffn_config::max_tokens - 1) * D + i] =
                (static_cast<int>(i % 11) - 5) * 0.13f;
        }
        const Weights first = random_weights(11);
        const unsigned int counts[] = {1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17};
        for (unsigned int count : counts) {
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
        const unsigned int tails[] = {9, 7, 1, 15, 17, 2, 659, 9};
        for (unsigned int count : tails) {
            for (std::size_t i = 0; i < count * D; ++i) {
                x[i] = (static_cast<int>((i + count) % 19) - 9) * 0.0625f;
            }
            run_case("repeated-tail/reload", count, x, random_weights(100 + count));
        }
        test_invalid(first);
        std::cout << "All full FFN reference tests passed (30 transactions).\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
