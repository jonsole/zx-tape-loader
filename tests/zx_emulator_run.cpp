// Runs lineloader on the zx-spectrum-emulator core, with no ROM but a stand-in
// ($F3 at 0, $FF at $3900-$3AFF), from CODE_START with the fast part of the
// tape playing. Reports the T-state within the line of every line routine's
// first instruction, and PRE's T-state within the frame, and writes
// screens and a memory dump.
//   run <basic.bin> <tape> <code_start> <entry> <max_frames> <outdir> <addr>...
#include "spectrum.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <map>
#include <set>
#include <string>
#include <vector>
using namespace zx;
static std::vector<uint8_t> slurp(const char* p) {
    std::ifstream f(p, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}
int main(int argc, char** argv) {
    if (argc < 8) { std::fprintf(stderr, "usage\n"); return 2; }
    auto basic = slurp(argv[1]);
    auto tape = slurp(argv[2]);
    uint16_t code_start = uint16_t(std::strtol(argv[3], nullptr, 0));
    uint16_t entry = uint16_t(std::strtol(argv[4], nullptr, 0));
    int max_frames = std::atoi(argv[5]);
    std::string outdir = argv[6];
    std::vector<uint16_t> watch;
    for (int i = 7; i < argc; i++) watch.push_back(uint16_t(std::strtol(argv[i], nullptr, 0)));
    uint16_t pre = watch.empty() ? 0 : watch[0];

    Spectrum m;
    std::vector<uint8_t> rom(16384, 0);
    rom[0] = 0xF3;
    for (int a = 0x3900; a < 0x3B00; a++) rom[a] = 0xFF;
    std::string err = m.load_rom(rom.data(), rom.size());
    if (!err.empty()) { std::fprintf(stderr, "%s\n", err.c_str()); return 2; }
    m.write_memory(23755, basic.data(), basic.size());
    Registers r;
    r.pc = code_start; r.sp = 0x6F00; r.im = 1; r.iy = 0x5C3A;
    m.set_registers(r);
    err = m.tape.insert(tape.data(), tape.size(), argv[2]);
    if (!err.empty()) { std::fprintf(stderr, "tape: %s\n", err.c_str()); return 2; }
    m.tape.set_fast_load(false);
    m.tape.play(m.global_hc());

    const uint64_t hc_line = 448, hc_frame = 69888ull * 2;
    std::map<uint16_t, std::set<uint64_t>> phase;  // routine -> T-state within the line
    std::set<uint64_t> pre_frame;
    uint64_t start = m.global_hc();
    bool reached = false;
    int shots = 0;
    uint64_t next_shot = 0;
    while (!reached) {
        uint64_t now = m.global_hc();
        if ((now - start) / hc_frame >= uint64_t(max_frames)) break;
        uint16_t pc = m.registers().pc;
        if (pc == entry) { reached = true; break; }
        for (size_t i = 0; i < watch.size(); i++)
            if (pc == watch[i]) {
                uint64_t t = now / 2;
                phase[pc].insert(t % 224);
                if (i == 0) pre_frame.insert(t % 69888);
                if (i == 0 && now >= next_shot && shots < 400) {
                    // a picture every 25 frames, from the frame just finished
                    std::ofstream o(outdir + "/frame" + std::to_string(shots++) + ".rgb", std::ios::binary);
                    const auto& s = m.screen();
                    o.write(reinterpret_cast<const char*>(s.data()), std::streamsize(s.size()));
                    next_shot = now + 25 * hc_frame;
                }
            }
        m.step_instruction();
    }
    std::printf("reached=%d frames=%llu pc=%04X\n", int(reached),
                (unsigned long long)((m.global_hc() - start) / hc_frame), m.registers().pc);
    for (auto& [pc, ph] : phase) {
        std::printf("%04X:", pc);
        int n = 0;
        for (auto p : ph) { if (n++ < 8) std::printf(" %llu", (unsigned long long)p); }
        std::printf(" (%zu distinct)\n", ph.size());
    }
    std::printf("PRE at frame T:");
    int n = 0;
    for (auto p : pre_frame) if (n++ < 8) std::printf(" %llu", (unsigned long long)p);
    std::printf(" (%zu distinct)\n", pre_frame.size());
    auto mem = m.read_memory(0, 65536);
    std::ofstream o(outdir + "/memory.bin", std::ios::binary);
    o.write(reinterpret_cast<const char*>(mem.data()), 65536);
    for (int i = 0; i < 3; i++) m.run_frame();
    std::ofstream f(outdir + "/final.rgb", std::ios::binary);
    f.write(reinterpret_cast<const char*>(m.screen().data()), std::streamsize(m.screen().size()));
    return reached ? 0 : 1;
}
