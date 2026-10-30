#ifndef PYNQ_FFN_KERNEL128_01_HPP
#define PYNQ_FFN_KERNEL128_01_HPP

namespace ffn_config {
constexpr unsigned int input_dim = 128;
constexpr unsigned int hidden_dim = 256;
constexpr unsigned int max_tokens = 660;
constexpr unsigned int output_lanes = 128;
constexpr unsigned int token_tile = 8;
static_assert(input_dim % output_lanes == 0 && hidden_dim % output_lanes == 0,
              "Output lanes must evenly tile both fixed dimensions.");
static_assert(token_tile == 8, "Flat context indexing requires eight token slots.");
}

// Nonoverlapping row-major float32 buffers, one weight set per invocation.
// Invalid counts return before any buffer access; host calls are serialized.
void ffn_accel(const float* x, float* y, const float* w1, const float* b1,
               const float* w2, const float* b2, unsigned int num_tokens);

#endif
