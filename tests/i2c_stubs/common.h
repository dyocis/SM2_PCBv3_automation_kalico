#ifndef SM2_TEST_COMMON_H
#define SM2_TEST_COMMON_H
#include <stdint.h>
#include <stddef.h>
#define CONFIG_WANT_GPIO_I2C 1
#define CONFIG_WANT_SOFTWARE_I2C 1
#define DECL_COMMAND(fn, fmt)
struct i2c_config { uint32_t unused; };
struct i2c_software { uint32_t unused; };
struct i2cdev_s {
    union { struct i2c_config i2c_hw; struct i2c_software *i2c_sw; };
    uint8_t flags;
};
enum { I2C_BUS_SUCCESS, I2C_BUS_NACK, I2C_BUS_TIMEOUT,
       I2C_BUS_START_NACK, I2C_BUS_START_READ_NACK };
void *oid_alloc(uint8_t, void *, size_t);
void *oid_lookup(uint8_t, void *);
void *command_decode_ptr(uint32_t);
void shutdown(const char *) __attribute__((noreturn));
void sendf(const char *, ...);
struct i2c_config i2c_setup(uint32_t, uint32_t, uint8_t);
int i2c_write(struct i2c_config, uint8_t, uint8_t *);
int i2c_read(struct i2c_config, uint8_t, uint8_t *, uint8_t, uint8_t *);
int i2c_software_write(struct i2c_software *, uint8_t, uint8_t *);
int i2c_software_read(struct i2c_software *, uint8_t, uint8_t *, uint8_t, uint8_t *);
#endif
