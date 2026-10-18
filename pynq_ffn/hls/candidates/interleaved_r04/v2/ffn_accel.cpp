#include "ffn_accel.hpp"

#if defined(__SYNTHESIS__) || defined(FFN_USE_HLS_MATH)
#include <hls_math.h>
#else
#include <cmath>
#endif

namespace {
float gelu(float value) {
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

    // Each valid call reloads the entire weight set once; host use is not reentrant.
    static float local_w1[ffn_config::input_dim][ffn_config::hidden_dim];
    static float local_w2[ffn_config::hidden_dim][ffn_config::input_dim];
#pragma HLS ARRAY_PARTITION variable=local_w1 cyclic factor=16 dim=2
#pragma HLS ARRAY_PARTITION variable=local_w2 cyclic factor=16 dim=2
    static float local_b1[ffn_config::hidden_dim];
    static float local_b2[ffn_config::input_dim];
    float token[ffn_config::token_tile][ffn_config::input_dim];
    float hidden[ffn_config::token_tile][ffn_config::hidden_dim];
#pragma HLS ARRAY_PARTITION variable=token complete dim=1
#pragma HLS ARRAY_PARTITION variable=hidden complete dim=1
#pragma HLS ARRAY_PARTITION variable=hidden cyclic factor=16 dim=2

    for (unsigned int i = 0; i < ffn_config::input_dim; ++i) {
        for (unsigned int j = 0; j < ffn_config::hidden_dim; ++j) {
#pragma HLS PIPELINE II=1
            local_w1[i][j] = w1[i * ffn_config::hidden_dim + j];
        }
    }
    for (unsigned int j = 0; j < ffn_config::hidden_dim; ++j) {
        for (unsigned int k = 0; k < ffn_config::input_dim; ++k) {
#pragma HLS PIPELINE II=1
            local_w2[j][k] = w2[j * ffn_config::input_dim + k];
        }
        local_b1[j] = b1[j];
    }
    for (unsigned int k = 0; k < ffn_config::input_dim; ++k) {
#pragma HLS PIPELINE II=1
        local_b2[k] = b2[k];
    }

    for (unsigned int base = 0; base < num_tokens; base += ffn_config::token_tile) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=83
        for (unsigned int ctx = 0; ctx < ffn_config::token_tile; ++ctx) {
            for (unsigned int i = 0; i < ffn_config::input_dim; ++i) {
#pragma HLS PIPELINE II=1
                // Inactive slots never access DDR, but retain the same eight-step schedule.
                token[ctx][i] = base + ctx < num_tokens
                    ? x[(base + ctx) * ffn_config::input_dim + i] : 0.0f;
            }
        }
        for (unsigned int j = 0; j < ffn_config::hidden_dim;
             j += ffn_config::output_lanes) {
            float sum[ffn_config::token_tile][ffn_config::output_lanes] = {};
#pragma HLS ARRAY_PARTITION variable=sum complete dim=0
        fc1_steps:
            for (unsigned int step = 0;
                 step < ffn_config::input_dim * ffn_config::token_tile; ++step) {
#pragma HLS PIPELINE II=1
#pragma HLS DEPENDENCE variable=sum type=inter direction=RAW distance=8 true
                const unsigned int ctx = step & 7;
                const unsigned int i = step >> 3;
                // Exactly one context is updated per step: the same sum recurs after eight steps.
                // All contexts reuse the same 16 weight banks, not eight simultaneous weight rows.
                const float input = token[ctx][i];
                for (unsigned int lane = 0; lane < ffn_config::output_lanes; ++lane) {
#pragma HLS UNROLL
                    float product;
                    float updated;
#pragma HLS BIND_OP variable=product op=fmul impl=maxdsp latency=3
#pragma HLS BIND_OP variable=updated op=fadd impl=fulldsp latency=6
                    product = input * local_w1[i][j + lane];
                    updated = sum[ctx][lane] + product;
                    sum[ctx][lane] = updated;
                }
            }
            for (unsigned int ctx = 0; ctx < ffn_config::token_tile; ++ctx) {
                for (unsigned int lane = 0; lane < ffn_config::output_lanes; ++lane) {
#pragma HLS PIPELINE II=1
                    hidden[ctx][j + lane] = sum[ctx][lane] + local_b1[j + lane];
                }
            }
        }
        // One shared exact-erf pipeline, after the complete ordered FC1 reductions.
        for (unsigned int step = 0;
             step < ffn_config::hidden_dim * ffn_config::token_tile; ++step) {
#pragma HLS PIPELINE II=1
            const unsigned int ctx = step / ffn_config::hidden_dim;
            const unsigned int j = step % ffn_config::hidden_dim;
            hidden[ctx][j] = gelu(hidden[ctx][j]);
        }
        for (unsigned int k = 0; k < ffn_config::input_dim;
             k += ffn_config::output_lanes) {
            float sum[ffn_config::token_tile][ffn_config::output_lanes] = {};
#pragma HLS ARRAY_PARTITION variable=sum complete dim=0
        fc2_steps:
            for (unsigned int step = 0;
                 step < ffn_config::hidden_dim * ffn_config::token_tile; ++step) {
#pragma HLS PIPELINE II=1
#pragma HLS DEPENDENCE variable=sum type=inter direction=RAW distance=8 true
                const unsigned int ctx = step & 7;
                const unsigned int j = step >> 3;
                const float input = hidden[ctx][j];
                for (unsigned int lane = 0; lane < ffn_config::output_lanes; ++lane) {
#pragma HLS UNROLL
                    float product;
                    float updated;
#pragma HLS BIND_OP variable=product op=fmul impl=maxdsp latency=3
#pragma HLS BIND_OP variable=updated op=fadd impl=fulldsp latency=6
                    product = input * local_w2[j][k + lane];
                    updated = sum[ctx][lane] + product;
                    sum[ctx][lane] = updated;
                }
            }
            for (unsigned int ctx = 0; ctx < ffn_config::token_tile; ++ctx) {
                for (unsigned int lane = 0; lane < ffn_config::output_lanes; ++lane) {
#pragma HLS PIPELINE II=1
                    if (base + ctx < num_tokens) {
                        y[(base + ctx) * ffn_config::input_dim + k + lane] =
                            sum[ctx][lane] + local_b2[k + lane];
                    }
                }
            }
        }
    }
}
