// Runs the actual pinned Kalico commands plus the proposed reporting command.
#include <assert.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "common.h"

static struct i2cdev_s device;
static uint8_t payload[32];
static int bus_result, calls, sent, status_value, response_len;
static jmp_buf shutdown_target;
static int expect_shutdown;

void *oid_alloc(uint8_t oid, void *handler, size_t size) { return &device; }
void *oid_lookup(uint8_t oid, void *handler) { assert(oid == 7); return &device; }
void *command_decode_ptr(uint32_t token) { assert(token == 1); return payload; }
void shutdown(const char *reason) {
    assert(expect_shutdown);
    longjmp(shutdown_target, 1);
}
void sendf(const char *format, ...) {
    va_list args;
    va_start(args, format);
    assert(va_arg(args, int) == 7);
    assert(strstr(format, "sm2_i2c_result_v1"));
    status_value = va_arg(args, int);
    response_len = va_arg(args, int);
    uint8_t *data = va_arg(args, uint8_t *);
    if (status_value) assert(response_len == 0);
    for (int i = 0; i < response_len; i++) assert(data[i] == 0xa5);
    sent++;
    va_end(args);
}
struct i2c_config i2c_setup(uint32_t bus, uint32_t rate, uint8_t addr) {
    return (struct i2c_config){0};
}
int i2c_write(struct i2c_config cfg, uint8_t n, uint8_t *data) {
    calls++;
    assert(n <= 32);
    return bus_result;
}
int i2c_read(struct i2c_config cfg, uint8_t n, uint8_t *reg,
             uint8_t len, uint8_t *data) {
    calls++;
    assert(len <= 32);
    // Deliberately fill even an error result: it must not escape the command.
    memset(data, 0xa5, len);
    return bus_result;
}
int i2c_software_write(struct i2c_software *bus, uint8_t n, uint8_t *data) {
    return i2c_write((struct i2c_config){0}, n, data);
}
int i2c_software_read(struct i2c_software *bus, uint8_t n, uint8_t *reg,
                       uint8_t len, uint8_t *data) {
    return i2c_read((struct i2c_config){0}, n, reg, len, data);
}

#include "candidate.c"

int main(void) {
    // Hardware and software I2C, reads and writes, all firmware statuses.
    for (int flags = 1; flags <= 2; flags++) {
        device.flags = flags;
        for (int reading = 0; reading <= 1; reading++) {
            for (bus_result = 0; bus_result <= 4; bus_result++) {
                uint32_t args[] = {7, 2, 1, reading ? 32 : 0};
                calls = sent = 0;
                command_sm2_i2c_report_v1(args);
                assert(calls == 1 && sent == 1);
                assert(status_value == bus_result);
                assert(response_len == (reading && !bus_result ? 32 : 0));
            }
        }
    }
    uint32_t invalid[][4] = {{7, 33, 1, 0}, {7, 1, 1, 33}, {7, 0, 1, 0}};
    for (unsigned i = 0; i < sizeof(invalid)/sizeof(invalid[0]); i++) {
        calls = sent = 0;
        command_sm2_i2c_report_v1(invalid[i]);
        assert(calls == 0 && sent == 1 && status_value == 5);
    }
    uint32_t args[] = {7, 1, 1, 3};
    device.flags = 0;
    calls = 0;
    command_sm2_i2c_report_v1(args);
    assert(calls == 0 && status_value == 6);
    device.flags = 2;
    // Legacy sensor commands MUST retain fatal error behavior.
    expect_shutdown = 1;
    for (bus_result = 1; bus_result <= 4; bus_result++) {
        if (!setjmp(shutdown_target)) { command_i2c_read(args); assert(0); }
        if (!setjmp(shutdown_target)) { command_i2c_write(args); assert(0); }
    }
    puts("C protocol checks passed: errors reported; legacy shutdowns preserved");
    return 0;
}
