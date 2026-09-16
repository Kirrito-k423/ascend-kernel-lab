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
}
// 参数必须是非空 UTF-8 字符串字面量；编号为编译期常量，不在设备上处理字符串。
#define AKL_DEBUG_CLOCK(recorder, ...) do { \
    constexpr uint32_t akl_event_id = akl::PathHash(2166136261u, __VA_ARGS__); \
    (recorder).Mark(akl_event_id); \
} while (false)
