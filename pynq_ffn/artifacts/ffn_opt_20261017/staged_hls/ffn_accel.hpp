#ifndef PYNQ_FFN_ACCEL_HPP
#define PYNQ_FFN_ACCEL_HPP

namespace ffn_config {
constexpr unsigned int input_dim = 128;
constexpr unsigned int hidden_dim = 256;
constexpr unsigned int max_tokens = 660;
constexpr unsigned int output_lanes = 4;
static_assert(output_lanes == 4, "Keep output lanes and cyclic partition factor in sync.");
static_assert(input_dim % output_lanes == 0 && hidden_dim % output_lanes == 0,
              "Output tiles must cover both fixed dimensions exactly.");
}

// Row-major, float32 buffers; one weight set per invocation.
// Invalid token counts (0 or >660) return without reading or writing buffers.
void ffn_accel(const float* x, float* y, const float* w1, const float* b1,
               const float* w2, const float* b2, unsigned int num_tokens);

#endif
