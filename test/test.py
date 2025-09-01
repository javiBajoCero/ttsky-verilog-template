# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0


import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge
from cocotb.utils import get_sim_time

@cocotb.test()
async def test_baud_tick(dut):
    dut._log.info(f"Simulating with BAUD_DIV = 651 (expected tick every 13 us at 50 MHz)")

    # Correct clock for @ 50 MHz
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())

    # Reset
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    dut._log.info("Reset released")

    # Observe uo_out[0]
    baud_tick_count = 0
    prev = 0
    max_cycles = 10_000  # Timeout after 10k cycles to avoid infinite loop

    for i in range(max_cycles):
        await RisingEdge(dut.clk)
        tick = (dut.uo_out.value.integer >> 1) & 0x01  # uo_out[1]
        if tick and not prev:
            baud_tick_count += 1
            dut._log.info(f"Tick {baud_tick_count} seen at cycle {i}")
            if baud_tick_count >= 10:
                break
        prev = tick

    assert baud_tick_count >= 10, f"Expected 10 baud ticks, got {baud_tick_count}"

@cocotb.test()
async def test_baud_tick_tx(dut):
    """Test baud_tick_tx (BAUD_DIV = 6666 @ 64 MHz) on uo_out[1]"""
    cocotb.start_soon(Clock(dut.clk, 15, units="ns").start())  # 64 MHz

    dut._log.info("Testing baud_tick_tx (BAUD_DIV = 6666, ~9600 baud) on uo_out[1]")

    # Reset
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    dut._log.info("Reset released")

    # Observe baud_tick_tx from uo_out[1]
    baud_tick_count = 0
    prev = 0
    max_cycles = 66_660  # Allow for ~10 full baud ticks (6666 * 10)

    for i in range(max_cycles):
        await RisingEdge(dut.clk)
        tick = (dut.uo_out.value.integer >> 2) & 0x01  # uo_out[2]
        if tick and not prev:
            baud_tick_count += 1
            dut._log.info(f"TX Tick {baud_tick_count} seen at cycle {i}")
            if baud_tick_count >= 10:
                break
        prev = tick

    assert baud_tick_count >= 10, f"Expected 10 TX baud ticks, got {baud_tick_count}"
    



def uart_encode(byte):
    """Returns UART frame as a list of bits: start + data (LSB first) + stop"""
    return [0] + [(byte >> i) & 1 for i in range(8)] + [1]


async def uart_rx_monitor(dut, expected_bits, bit_period_clk):
    """Coroutine to receive UART bits in real time"""
    received_bits = []
    received_timestamps = []
    IDLE_LINE = 1
    skip_first_bit = True

    while len(received_bits) < expected_bits:
        await RisingEdge(dut.clk)
        bit = int(dut.uo_out.value) & 1
        if bit != IDLE_LINE:  # Start bit detected
            if skip_first_bit:
                skip_first_bit = False
                dut._log.warning("Skipped first UART start bit (intentional)")
                await ClockCycles(dut.clk, 2000)
                continue

            tstart = get_sim_time(units="ns")
            received_bits.append(bit)
            received_timestamps.append(tstart)
            dut._log.info(f"Start Bit {len(received_bits) - 1}: {bit} at {tstart} ns")
            await ClockCycles(dut.clk, 5)

            for i in range(8 + 1):  # 8 data + 1 stop
                await ClockCycles(dut.clk, bit_period_clk)
                bit = int(dut.uo_out.value) & 1
                timestamp = get_sim_time(units="ns")
                received_bits.append(bit)
                received_timestamps.append(timestamp)
                if i == 8:
                    dut._log.info(f"End Bit {len(received_bits) - 1}: {bit} at {timestamp} ns")
                    dut._log.info(f"Length of byte: {timestamp - tstart} ns")
                else:
                    dut._log.info(f"TX Bit {len(received_bits) - 1}: {bit} at {timestamp} ns")

    return received_bits


@cocotb.test()
async def test_uart_tx(dut):
    """Send 'MARCO' to RX and check if '\\n\\rPOLO!\\n\\r' is transmitted"""

    cocotb.start_soon(Clock(dut.clk, 15, units="ns").start())  # 64 MHz

    # Reset
    dut.rst_n.value = 0
    dut.ui_in.value = 0xFF  # All lines high (idle)
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    dut._log.info("Reset released")

    # Keep RX line idle high before transmission
    dut.ui_in[0].value = 1
    await ClockCycles(dut.clk, 100)

    # Constants for timing
    oversample_tick_cycles = 833
    bits_per_uart_bit = 8
    bit_duration = oversample_tick_cycles * bits_per_uart_bit  # 6666 cycles

    # Start UART RX monitor before TX starts
    expected_bits = 9 * 10  # 9 bytes: 10 bits each (start+8data+stop)
    rx_task = cocotb.start_soon(uart_rx_monitor(dut, expected_bits, bit_duration))

    # Send "MARCO" to trigger UART TX response
    for ch in "MARCO":
        bits = uart_encode(ord(ch))
        for bit in bits:
            dut.ui_in[0].value = bit
            await ClockCycles(dut.clk, bit_duration)

        # Hold line idle for stop bit and inter-byte delay (2 bit times)
        dut.ui_in[0].value = 1
        await ClockCycles(dut.clk, bit_duration * 2)
        timestamp = get_sim_time(units="ns")
        dut._log.info(f"Sent char: {ch}, {timestamp} ns")

    # Wait for trigger_send signal (uo_out[3])
    dut._log.info("Sent all bytes, checking for trigger and RX activity")
    for _ in range(10000):
        await RisingEdge(dut.clk)
        if dut.uo_out.value[3]:  # trigger_send
            timestamp = get_sim_time(units="ns")
            dut._log.info(f"TRIGGER MATCHED {timestamp} ns! TX should start soon.")
            break
    else:
        assert False, "Trigger match never happened"

    # Wait for RX monitor to finish
    received_bits = await rx_task

    # Decode UART frames
    def decode_uart(bits):
        bytes_out = []
        for i in range(0, len(bits), 10):
            if i + 9 >= len(bits):
                break  # incomplete frame
            start_bit = bits[i]
            stop_bit = bits[i + 9]
            if start_bit != 0 or stop_bit != 1:
                dut._log.warning(f"Frame {i//10} framing error: start={start_bit}, stop={stop_bit}")
                continue
            byte_val = 0
            for b in range(8):
                byte_val |= bits[i + 1 + b] << b
            bytes_out.append(byte_val)
            dut._log.info(f"Frame {i//10}: start={start_bit}, stop={stop_bit}")
        return bytes_out

    received_bytes = decode_uart(received_bits)
    received_chars = [chr(b) for b in received_bytes]
    dut._log.info(f"Received bytes: {received_chars}")

    expected_bytes = [ord(c) for c in "\n\rPOLO!\n\r"]
    assert received_bytes == expected_bytes, f"Expected {expected_bytes}, got {received_bytes}"
