
// Optional SM2 ambient-sensor transaction reporting, protocol version 1.
// Copyright (C) 2026 David Yocis
// Distributed under the GNU GPLv3 license.
// Appended to the pinned Kalico src/i2ccmds.c by prepare_sm2_i2c.py.
// Existing i2c_read/i2c_write and shutdown behavior are unchanged.
#define SM2_I2C_REPORT_MAX 32
#define SM2_I2C_REPORT_INVALID_LENGTH 5
#define SM2_I2C_REPORT_UNCONFIGURED 6

void
command_sm2_i2c_report_v1(uint32_t *args)
{
    uint8_t oid = args[0];
    struct i2cdev_s *i2c = i2cdev_oid_lookup(oid);
    uint32_t write_len = args[1], read_len = args[3];
    uint8_t data[SM2_I2C_REPORT_MAX] = {0};
    int ret;
    if (write_len > SM2_I2C_REPORT_MAX || read_len > SM2_I2C_REPORT_MAX
        || (!write_len && !read_len)) {
        ret = SM2_I2C_REPORT_INVALID_LENGTH;
    } else if (!(i2c->flags & (IF_SOFTWARE | IF_HARDWARE))) {
        ret = SM2_I2C_REPORT_UNCONFIGURED;
    } else {
        uint8_t *write = command_decode_ptr(args[2]);
        if (read_len)
            ret = i2c_dev_read(i2c, write_len, write, read_len, data);
        else
            ret = i2c_dev_write(i2c, write_len, write);
    }
    // Never return partial or uninitialized data after a failed transaction.
    uint8_t response_len = ret == I2C_BUS_SUCCESS ? read_len : 0;
    sendf("sm2_i2c_result_v1 oid=%c status=%c response=%*s",
          oid, ret, response_len, data);
}
DECL_COMMAND(command_sm2_i2c_report_v1,
             "sm2_i2c_report_v1 oid=%c data=%*s read_len=%c");
