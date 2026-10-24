# Opt-in SM2 ambient-sensor I2C transport. Not automatically installed/enabled.
# Copyright (C) 2026 David Yocis
# Distributed under the GNU GPLv3 license.

COMMAND = "sm2_i2c_report_v1 oid=%c data=%*s read_len=%c"
RESPONSE = "sm2_i2c_result_v1 oid=%c status=%c response=%*s"
MAX_LENGTH = 32
STATUS_NAMES = {1: "NACK", 2: "TIMEOUT", 3: "START_NACK",
                4: "START_READ_NACK", 5: "INVALID_LENGTH", 6: "UNCONFIGURED"}


class SM2I2CError(Exception):
    """No valid measurement is available from this transaction."""


class SM2I2CReport:
    """Adapter for an explicitly opted-in, non-critical ambient sensor bus.

    Drivers must construct this before MCU configuration and catch errors in
    their own measurement/retry lifecycle. Never fall back to fatal commands.
    This is transport, not sample validity or physical bus recovery.
    """

    def __init__(self, i2c):
        self.i2c = i2c
        self.mcu = i2c.get_mcu()
        self.i2c_address = i2c.get_i2c_address()
        if not getattr(self.mcu, "is_non_critical", False):
            raise SM2I2CError("SM2 reporting requires a non-critical MCU")
        if self.i2c_address not in (0x59, 0x76, 0x77):
            raise SM2I2CError("SM2 reporting supports only SGP40/BME280 addresses")
        self.command = None
        self.mcu.register_config_callback(self.build_config)

    def get_mcu(self):
        return self.mcu

    def get_oid(self):
        return self.i2c.get_oid()

    def get_i2c_address(self):
        return self.i2c_address

    def build_config(self):
        self.command = None
        # lookup_query_command validates both request and reply formats;
        # unsupported firmware must fail configuration, never use old commands.
        self.command = self.mcu.lookup_query_command(
            COMMAND, RESPONSE, oid=self.get_oid(),
            cq=self.i2c.get_command_queue())

    def _transfer(self, data, read_len, minclock=0, reqclock=0):
        if (self.command is None
                or self.mcu.non_critical_disconnected
                or getattr(self.mcu, "_reconnecting", False)):
            raise SM2I2CError("SM2 I2C unavailable")
        data = bytes(data)
        if (not isinstance(read_len, int) or not 0 <= read_len <= MAX_LENGTH
                or len(data) > MAX_LENGTH or not (data or read_len)):
            raise SM2I2CError("Invalid SM2 I2C transfer length")
        # No automatic replay: writes can start measurements/reset sensors.
        reply = self.command.send([self.get_oid(), data, read_len],
                                  minclock=minclock, reqclock=reqclock,
                                  retry=False)
        if (self.mcu.non_critical_disconnected
                or getattr(self.mcu, "_reconnecting", False)):
            raise SM2I2CError("SM2 disconnected during I2C transaction")
        if reply.get("oid") != self.get_oid():
            raise SM2I2CError("Invalid SM2 I2C response OID")
        status = reply.get("status")
        if status != 0:
            raise SM2I2CError("SM2 I2C %s" % STATUS_NAMES.get(status, "UNKNOWN_ERROR"))
        response = reply.get("response")
        if not isinstance(response, (bytes, bytearray)) or len(response) != read_len:
            raise SM2I2CError("Incomplete SM2 I2C response")
        return reply

    def i2c_write(self, data, minclock=0, reqclock=0):
        self._transfer(data, 0, minclock, reqclock)

    def i2c_read(self, write, read_len, retry=False):
        return self._transfer(write, read_len)
