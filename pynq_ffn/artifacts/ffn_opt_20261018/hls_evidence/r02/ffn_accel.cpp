#include "ffn_accel.hpp"

#if defined(__SYNTHESIS__) || defined(FFN_USE_HLS_MATH)
#include <hls_math.h>
#else
#include <cmath>
#endif

namespace {
float gelu(float value) {
    // Mathematical erf GELU, not the commonly used tanh approximation.
    const float inv_sqrt_two = 0.70710678118654752440f;
#if defined(__SYNTHESIS__) || defined(FFN_USE_HLS_MATH)
    const float erf_value = hls::erff(value * inv_sqrt_two);
#else
    const float erf_value = std::erf(value * inv_sqrt_two);
#endif
    return (0.5f * value) * (1.0f + erf_value);
}
}

void ffn_accel(const float* x, float* y, const float* w1, const float* b1,
               const float* w2, const float* b2, unsigned int num_tokens) {
#pragma HLS INTERFACE m_axi port=x offset=slave bundle=gmem0 depth=84480
#pragma HLS INTERFACE m_axi port=y offset=slave bundle=gmem0 depth=84480
#pragma HLS INTERFACE m_axi port=w1 offset=slave bundle=gmem1 depth=32768
#pragma HLS INTERFACE m_axi port=b1 offset=slave bundle=gmem1 depth=256
#pragma HLS INTERFACE m_axi port=w2 offset=slave bundle=gmem1 depth=32768
#pragma HLS INTERFACE m_axi port=b2 offset=slave bundle=gmem1 depth=128
#pragma HLS INTERFACE s_axilite port=x bundle=control offset=0x10
#pragma HLS INTERFACE s_axilite port=y bundle=control offset=0x1c
#pragma HLS INTERFACE s_axilite port=w1 bundle=control offset=0x28
#pragma HLS INTERFACE s_axilite port=b1 bundle=control offset=0x34
#pragma HLS INTERFACE s_axilite port=w2 bundle=control offset=0x40
#pragma HLS INTERFACE s_axilite port=b2 bundle=control offset=0x4c
#pragma HLS INTERFACE s_axilite port=num_tokens bundle=control offset=0x58
#pragma HLS INTERFACE s_axilite port=return bundle=control offset=0x00

    if (num_tokens == 0 || num_tokens > ffn_config::max_tokens) {
        return;
    }

    // Static storage avoids a large host-test stack frame. Every valid call
    // overwrites all weights/biases before use. The host entry is not reentrant.
    static float local_w1[ffn_config::input_dim][ffn_config::hidden_dim];
    static float local_w2[ffn_config::hidden_dim][ffn_config::input_dim];
#pragma HLS ARRAY_PARTITION variable=local_w1 cyclic factor=4 dim=2
#pragma HLS ARRAY_PARTITION variable=local_w2 cyclic factor=4 dim=2
    static float local_b1[ffn_config::hidden_dim];
    static float local_b2[ffn_config::input_dim];
    float token[ffn_config::input_dim];
    float hidden[ffn_config::hidden_dim];

    for (unsigned int i = 0; i < ffn_config::input_dim; ++i) {
        for (unsigned int j = 0; j < ffn_config::hidden_dim; ++j) {
#pragma HLS PIPELINE
            local_w1[i][j] = w1[i * ffn_config::hidden_dim + j];
        }
    }
    for (unsigned int j = 0; j < ffn_config::hidden_dim; ++j) {
        for (unsigned int k = 0; k < ffn_config::input_dim; ++k) {
#pragma HLS PIPELINE
            local_w2[j][k] = w2[j * ffn_config::input_dim + k];
        }
        local_b1[j] = b1[j];
    }
    for (unsigned int k = 0; k < ffn_config::input_dim; ++k) {
#pragma HLS PIPELINE
        local_b2[k] = b2[k];
    }

    for (unsigned int t = 0; t < num_tokens; ++t) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=660
        for (unsigned int i = 0; i < ffn_config::input_dim; ++i) {
#pragma HLS PIPELINE
            token[i] = x[t * ffn_config::input_dim + i];
        }
        for (unsigned int j = 0; j < ffn_config::hidden_dim;
             j += ffn_config::output_lanes) {
            float sum[ffn_config::output_lanes] = {};
#pragma HLS ARRAY_PARTITION variable=sum complete dim=1
            for (unsigned int i = 0; i < ffn_config::input_dim; ++i) {
#pragma HLS PIPELINE
                // Each lane retains the original increasing-i FP32 reduction.
                // No II request: r01 showed a forced II=1 makes HLS compress the
                // float-add latency and report -18.4 ns slack at 10 ns.
                for (unsigned int lane = 0; lane < ffn_config::output_lanes; ++lane) {
#pragma HLS UNROLL
                    sum[lane] += token[i] * local_w1[i][j + lane];
                }
            }
            for (unsigned int lane = 0; lane < ffn_config::output_lanes; ++lane) {
#pragma HLS UNROLL off=true
                hidden[j + lane] = sum[lane] + local_b1[j + lane];
            }
        }
        // One pipelined pass shares the exact-erf unit across all outputs;
        // per-output math order is unchanged versus the grouped epilogue.
        for (unsigned int j = 0; j < ffn_config::hidden_dim; ++j) {
#pragma HLS PIPELINE
            hidden[j] = gelu(hidden[j]);
        }
        for (unsigned int k = 0; k < ffn_config::input_dim;
             k += ffn_config::output_lanes) {
            float sum[ffn_config::output_lanes] = {};
#pragma HLS ARRAY_PARTITION variable=sum complete dim=1
            for (unsigned int j = 0; j < ffn_config::hidden_dim; ++j) {
#pragma HLS PIPELINE
                for (unsigned int lane = 0; lane < ffn_config::output_lanes; ++lane) {
#pragma HLS UNROLL
                    sum[lane] += hidden[j] * local_w2[j][k + lane];
                }
            }
            for (unsigned int lane = 0; lane < ffn_config::output_lanes; ++lane) {
#pragma HLS UNROLL off=true
                y[t * ffn_config::input_dim + k + lane] = sum[lane] + local_b2[k + lane];
            }
        }
    }
}
