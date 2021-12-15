#!/usr/bin/env python
"""

Copyright (c) 2015-2018 Alex Forencich

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

"""

from myhdl import *
import os
import struct
import zlib

import axis_ep
import eth_ep

module = 'axis_eth_fcs_check'
testbench = 'test_%s' % module

srcs = []

srcs.append("../rtl/%s.v" % module)
srcs.append("../rtl/ve_lfsr.v")
srcs.append("%s.v" % testbench)

src = ' '.join(srcs)

build_cmd = "iverilog -o %s.vvp %s" % (testbench, src)

def bench():

    # Parameters


    # Inputs
    clk = Signal(bool(0))
    rst = Signal(bool(0))
    current_test = Signal(intbv(0)[8:])

    s_axis_tdata = Signal(intbv(0)[8:])
    s_axis_tvalid = Signal(bool(0))
    s_axis_tlast = Signal(bool(0))
    s_axis_tuser = Signal(bool(0))
    m_axis_tready = Signal(bool(0))

    # Outputs
    s_axis_tready = Signal(bool(0))
    m_axis_tdata = Signal(intbv(0)[8:])
    m_axis_tvalid = Signal(bool(0))
    m_axis_tlast = Signal(bool(0))
    m_axis_tuser = Signal(bool(0))
    busy = Signal(bool(0))
    error_bad_fcs = Signal(bool(0))

    # sources and sinks
    source_pause = Signal(bool(0))
    sink_pause = Signal(bool(0))

    source = axis_ep.AXIStreamSource()

    source_logic = source.create_logic(
        clk,
        rst,
        tdata=s_axis_tdata,
        tvalid=s_axis_tvalid,
        tready=s_axis_tready,
        tlast=s_axis_tlast,
        tuser=s_axis_tuser,
        pause=source_pause,
        name='source'
    )

    sink = axis_ep.AXIStreamSink()

    sink_logic = sink.create_logic(
        clk,
        rst,
        tdata=m_axis_tdata,
        tvalid=m_axis_tvalid,
        tready=m_axis_tready,
        tlast=m_axis_tlast,
        tuser=m_axis_tuser,
        pause=sink_pause,
        name='sink'
    )

    # DUT
    if os.system(build_cmd):
        raise Exception("Error running build command")

    dut = Cosimulation(
        "vvp -m myhdl %s.vvp -lxt2" % testbench,
        clk=clk,
        rst=rst,
        current_test=current_test,

        s_axis_tdata=s_axis_tdata,
        s_axis_tvalid=s_axis_tvalid,
        s_axis_tready=s_axis_tready,
        s_axis_tlast=s_axis_tlast,
        s_axis_tuser=s_axis_tuser,

        m_axis_tdata=m_axis_tdata,
        m_axis_tvalid=m_axis_tvalid,
        m_axis_tready=m_axis_tready,
        m_axis_tlast=m_axis_tlast,
        m_axis_tuser=m_axis_tuser,

        busy=busy,
        error_bad_fcs=error_bad_fcs
    )

    @always(delay(4))
    def clkgen():
        clk.next = not clk

    error_bad_fcs_asserted = Signal(bool(0))

    @always(clk.posedge)
    def monitor():
        if (error_bad_fcs):
            error_bad_fcs_asserted.next = 1

    def wait_normal():
        while s_axis_tvalid or m_axis_tvalid:
            yield clk.posedge

    def wait_pause_source():
        while s_axis_tvalid or m_axis_tvalid:
            yield clk.posedge
            yield clk.posedge
            source_pause.next = False
            yield clk.posedge
            source_pause.next = True
            yield clk.posedge

        source_pause.next = False

    def wait_pause_sink():
        while s_axis_tvalid or m_axis_tvalid:
            sink_pause.next = True
            yield clk.posedge
            yield clk.posedge
            yield clk.posedge
            sink_pause.next = False
            yield clk.posedge

    @instance
    def check():
        yield delay(100)
        yield clk.posedge
        rst.next = 1
        yield clk.posedge
        rst.next = 0
        yield clk.posedge
        yield delay(100)
        yield clk.posedge

        # testbench stimulus

        for payload_len in list(range(1,18))+list(range(64,82)):
            yield clk.posedge
            print("test 1: test packet, length %d" % payload_len)
            current_test.next = 1

            test_frame = eth_ep.EthFrame()
            test_frame.eth_dest_mac = 0xDAD1D2D3D4D5
            test_frame.eth_src_mac = 0x5A5152535455
            test_frame.eth_type = 0x8000
            test_frame.payload = bytearray(range(payload_len))
            test_frame.update_fcs()

            axis_frame = test_frame.build_axis_fcs()

            for wait in wait_normal, wait_pause_source, wait_pause_sink:
                source.send(axis_frame)
                yield clk.posedge
                yield clk.posedge

                yield wait()

                yield sink.wait()
                rx_frame = sink.recv()

                eth_frame = eth_ep.EthFrame()
                eth_frame.parse_axis(rx_frame)
                eth_frame.update_fcs()

                assert eth_frame == test_frame
                assert not rx_frame.user[-1]

                assert sink.empty()

                yield delay(100)

            yield clk.posedge
            print("test 2: back-to-back packets, length %d" % payload_len)
            current_test.next = 2

            test_frame1 = eth_ep.EthFrame()
            test_frame1.eth_dest_mac = 0xDAD1D2D3D4D5
            test_frame1.eth_src_mac = 0x5A5152535455
            test_frame1.eth_type = 0x8000
            test_frame1.payload = bytearray(range(payload_len))
            test_frame1.update_fcs()
            test_frame2 = eth_ep.EthFrame()
            test_frame2.eth_dest_mac = 0xDAD1D2D3D4D5
            test_frame2.eth_src_mac = 0x5A5152535455
            test_frame2.eth_type = 0x8000
            test_frame2.payload = bytearray(range(payload_len))
            test_frame2.update_fcs()

            axis_frame1 = test_frame1.build_axis_fcs()
            axis_frame2 = test_frame2.build_axis_fcs()

            for wait in wait_normal, wait_pause_source, wait_pause_sink:
                source.send(axis_frame1)
                source.send(axis_frame2)
                yield clk.posedge
                yield clk.posedge

                yield wait()

                yield sink.wait()
                rx_frame = sink.recv()

                eth_frame = eth_ep.EthFrame()
                eth_frame.parse_axis(rx_frame)
                eth_frame.update_fcs()

                assert eth_frame == test_frame1
                assert not rx_frame.user[-1]

                yield sink.wait()
                rx_frame = sink.recv()

                eth_frame = eth_ep.EthFrame()
                eth_frame.parse_axis(rx_frame)
                eth_frame.update_fcs()

                assert eth_frame == test_frame2
                assert not rx_frame.user[-1]

                assert sink.empty()

                yield delay(100)

            yield clk.posedge
            print("test 3: tuser assert, length %d" % payload_len)
            current_test.next = 3

            test_frame1 = eth_ep.EthFrame()
            test_frame1.eth_dest_mac = 0xDAD1D2D3D4D5
            test_frame1.eth_src_mac = 0x5A5152535455
            test_frame1.eth_type = 0x8000
            test_frame1.payload = bytearray(range(payload_len))
            test_frame1.update_fcs()
            test_frame2 = eth_ep.EthFrame()
            test_frame2.eth_dest_mac = 0xDAD1D2D3D4D5
            test_frame2.eth_src_mac = 0x5A5152535455
            test_frame2.eth_type = 0x8000
            test_frame2.payload = bytearray(range(payload_len))
            test_frame2.update_fcs()

            axis_frame1 = test_frame1.build_axis_fcs()
            axis_frame2 = test_frame2.build_axis_fcs()

            axis_frame1.user = 1

            for wait in wait_normal, wait_pause_source, wait_pause_sink:
                source.send(axis_frame1)
                source.send(axis_frame2)
                yield clk.posedge
                yield clk.posedge

                yield wait()

                yield sink.wait()
                rx_frame = sink.recv()

                assert rx_frame.user[-1]

                yield sink.wait()
                rx_frame = sink.recv()

                eth_frame = eth_ep.EthFrame()
                eth_frame.parse_axis(rx_frame)
                eth_frame.update_fcs()

                assert eth_frame == test_frame2
                assert not rx_frame.user[-1]

                assert sink.empty()

                yield delay(100)

            yield clk.posedge
            print("test 4: bad FCS, length %d" % payload_len)
            current_test.next = 4

            test_frame1 = eth_ep.EthFrame()
            test_frame1.eth_dest_mac = 0xDAD1D2D3D4D5
            test_frame1.eth_src_mac = 0x5A5152535455
            test_frame1.eth_type = 0x8000
            test_frame1.payload = bytearray(range(payload_len))
            test_frame1.update_fcs()
            test_frame2 = eth_ep.EthFrame()
            test_frame2.eth_dest_mac = 0xDAD1D2D3D4D5
            test_frame2.eth_src_mac = 0x5A5152535455
            test_frame2.eth_type = 0x8000
            test_frame2.payload = bytearray(range(payload_len))
            test_frame2.update_fcs()

            axis_frame1 = test_frame1.build_axis_fcs()
            axis_frame2 = test_frame2.build_axis_fcs()

            axis_frame1.data[-1] ^= 0xff

            for wait in wait_normal, wait_pause_source, wait_pause_sink:
                error_bad_fcs_asserted.next = 0

                source.send(axis_frame1)
                source.send(axis_frame2)
                yield clk.posedge
                yield clk.posedge

                yield wait()

                yield sink.wait()
                rx_frame = sink.recv()

                assert error_bad_fcs_asserted

                assert rx_frame.user[-1]

                yield sink.wait()
                rx_frame = sink.recv()

                eth_frame = eth_ep.EthFrame()
                eth_frame.parse_axis(rx_frame)
                eth_frame.update_fcs()

                assert eth_frame == test_frame2
                assert not rx_frame.user[-1]

                assert sink.empty()

                yield delay(100)

        for payload_len in list(range(1,18)):
            yield clk.posedge
            print("test 5: test short packet, length %d" % payload_len)
            current_test.next = 5

            test_frame = bytearray(range(payload_len))
            fcs = zlib.crc32(bytes(test_frame)) & 0xffffffff
            test_frame_fcs = test_frame + struct.pack('<L', fcs)

            for wait in wait_normal, wait_pause_source, wait_pause_sink:
                source.send(test_frame_fcs)
                yield clk.posedge
                yield clk.posedge

                yield wait()

                yield sink.wait()
                rx_frame = sink.recv()

                assert test_frame == bytearray(rx_frame)

                assert sink.empty()

                yield delay(100)

        raise StopSimulation

    return instances()

def test_bench():
    sim = Simulation(bench())
    sim.run()

if __name__ == '__main__':
    print("Running test...")
    test_bench()
