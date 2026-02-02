import time
import queue
import rtmidi
import serial
import threading
import logging
import sys
import argparse

# Serial MIDI Bridge
# Original work Copyright 2020 Ryan Kojima
# Modified work Copyright 2024 Robert Hargreaves
# Licensed under Apache License 2.0


class midi_input_handler(object):
    def __init__(self, port, midiin_message_queue):
        self.port = port
        self._wallclock = time.time()
        self.midiin_message_queue = midiin_message_queue

    def __call__(self, event, data=None):
        message, deltatime = event
        self._wallclock += deltatime
        # logging.debug("[%s] @%0.6f %r" % (self.port, self._wallclock, message))
        self.midiin_message_queue.put(message)


def get_midi_length(message):
    if len(message) == 0:
        return 100
    opcode = message[0]
    if opcode >= 0xF4:
        return 1
    if opcode in [0xF1, 0xF3]:
        return 2
    if opcode == 0xF2:
        return 3
    if opcode == 0xF0:
        if message[-1] == 0xF7:
            return len(message)

    opcode = opcode & 0xF0
    if opcode in [0x80, 0x90, 0xA0, 0xB0, 0xE0]:
        return 3
    if opcode in [0xC0, 0xD0]:
        return 2

    return 100


def wrap_message_for_mega_pro(message):
    header = bytes([0x2B, 0xD4, 0x1A, 0xE5, 0x01, 0x81, 0x00, 0x00])
    len_bytes = len(message).to_bytes(4, "big")
    padding = bytes([0x00])
    return header + len_bytes + padding + message


class SerialMidi:
    def __init__(self, args):
        self.thread_running = True
        self.serial_port_name = args.serial_name  #'/dev/cu.SLAB_USBtoUART'
        self.serial_baud = args.baud
        self.given_port_name_in = args.midi_in_name  # "IAC Bus 1"
        self.given_port_name_out = args.midi_out_name  # "IAC Bus 2"
        self.everdrive_pro = args.everdrive_pro
        self.string = args.string
        self.midi_ready = False
        self.midiin_message_queue = queue.Queue()
        self.midiout_message_queue = queue.Queue()

    def serial_writer(self):
        while not self.midi_ready:
            time.sleep(0.1)
        while self.thread_running:
            try:
                message = self.midiin_message_queue.get(timeout=0.4)
            except queue.Empty:
                continue
            logging.debug("out: " + str(message))
            value = bytearray(message)
            if self.everdrive_pro:
                value = wrap_message_for_mega_pro(value)
            self.ser.write(value)

    def serial_watcher(self):
        receiving_message = []
        running_status = 0

        while not self.midi_ready:
            time.sleep(0.1)

        while self.thread_running:
            data = self.ser.read()
            if data:
                for elem in data:
                    receiving_message.append(elem)
                # Running status
                if len(receiving_message) == 1:
                    if (receiving_message[0] & 0xF0) != 0:
                        running_status = receiving_message[0]
                    else:
                        receiving_message = [running_status, receiving_message[0]]

                message_length = get_midi_length(receiving_message)
                if message_length <= len(receiving_message):
                    logging.debug("in: " + str(receiving_message))
                    self.midiout_message_queue.put(receiving_message)

                    if self.string:
                        if receiving_message[0] == 0xF0:
                            print_message = []
                            for elem in receiving_message:
                                if elem < 0xF0:
                                    print_message.append(chr(elem))
                            print_message_str = "".join(print_message)
                            print(print_message_str)
                    receiving_message = []

    def midi_watcher(self):
        midiin = rtmidi.MidiIn()
        midiout = rtmidi.MidiOut()
        available_ports_out = midiout.get_ports()
        available_ports_in = midiin.get_ports()
        logging.info("IN : '" + "','".join(available_ports_in) + "'")
        logging.info("OUT : '" + "','".join(available_ports_out) + "'")
        if self.everdrive_pro:
            logging.info("Mega Everdrive PRO mode enabled")
        logging.info("Hit ctrl-c to exit")

        port_index_in = -1
        port_index_out = -1
        for i, s in enumerate(available_ports_in):
            if self.given_port_name_in in s:
                port_index_in = i
        for i, s in enumerate(available_ports_out):
            if self.given_port_name_out in s:
                port_index_out = i

        if port_index_in == -1:
            print("MIDI IN Device name is incorrect. Please use listed device name.")
        if port_index_out == -1:
            print("MIDI OUT Device name is incorrect. Please use listed device name.")
        if port_index_in == -1 or port_index_out == -1:
            self.thread_running = False
            self.midi_ready = True
            sys.exit()

        midiout.open_port(port_index_out)
        in_port_name = midiin.open_port(port_index_in)

        self.midi_ready = True

        midiin.ignore_types(sysex=False, timing=False, active_sense=False)
        midiin.set_callback(midi_input_handler(in_port_name, self.midiin_message_queue))

        while self.thread_running:
            try:
                message = self.midiout_message_queue.get(timeout=0.4)
            except queue.Empty:
                continue
            midiout.send_message(message)

    def start(self):
        try:
            self.ser = serial.Serial(self.serial_port_name, self.serial_baud)
        except serial.serialutil.SerialException:
            print("Serial port opening error.")
            self.midi_watcher()
            sys.exit()

        self.ser.timeout = 0.4

        self.s_watcher = threading.Thread(target=self.serial_watcher)
        self.s_writer = threading.Thread(target=self.serial_writer)
        self.m_watcher = threading.Thread(target=self.midi_watcher)

        self.s_watcher.start()
        self.s_writer.start()
        self.m_watcher.start()

    def stop(self):
        self.thread_running = False


def parse_args():
    parser = argparse.ArgumentParser(description="Serial MIDI bridge")
    parser.add_argument(
        "--serial_name", type=str, required=True, help="Serial port name. Required"
    )
    parser.add_argument(
        "--baud", type=int, default=115200, help="baud rate. Default is 115200"
    )
    parser.add_argument("--midi_in_name", type=str, default="IAC Bus 1")
    parser.add_argument("--midi_out_name", type=str, default="IAC Bus 2")
    parser.add_argument(
        "--debug", action="store_true", help="Print incoming / outgoing MIDI signals"
    )
    parser.add_argument(
        "--string",
        action="store_true",
        help="Print sysEx logging message (For Qun Mk2)",
    )
    parser.add_argument(
        "--everdrive_pro",
        action="store_true",
        help="Format serial data for delivery to the Mega Everdrive PRO",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    serialmidi = SerialMidi(args)
    serialmidi.start()

    # Ctrl-C handler
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Terminating.")
        serialmidi.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
