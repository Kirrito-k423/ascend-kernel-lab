#pragma once
#include "akl/trace/recorder.h"

namespace akl {
__aicore__ constexpr uint32_t PathHash(uint32_t hash) { return hash; }
template<typename Char, typename... Tail>
__aicore__ constexpr uint32_t PathHash(uint32_t hash, const Char* part, Tail... tail) {
    // 保留 Bisheng 字符串字面量的 __gm__ 地址空间；不能强转为普通 char*。
    // FNV-1a 的 uint32 回绕是协议的一部分；每级追加 NUL，区分 [ab,c] 与 [a,bc]。
    for (; *part; ++part) hash = (hash ^ static_cast<unsigned char>(*part)) * 16777619u;
    return PathHash(hash * 16777619u, tail...);
}
template<class Recorder>
__aicore__ inline void Clock(Recorder& recorder, uint32_t hash) { recorder.Mark(hash); }
template<class Recorder, typename Number, typename Char,
         std::enable_if_t<std::is_arithmetic_v<Number>, int> = 0>
__aicore__ inline void Clock(Recorder& recorder, uint32_t hash, Number amount, const Char* unit) {
    recorder.Work(PathHash(hash, "@quantity", unit), amount);
}
template<class Recorder, typename Char, typename... Tail>
__aicore__ inline void Clock(Recorder& recorder, uint32_t hash, const Char* part, Tail... tail) {
    Clock(recorder, PathHash(hash, part), tail...);
}
}
// 字符串字面量参与 ID；可选末尾为处理量表达式与单位字面量。
#define AKL_DEBUG_CLOCK(recorder, ...) akl::Clock(recorder, 2166136261u, __VA_ARGS__)
