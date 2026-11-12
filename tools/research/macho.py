# SPDX-License-Identifier: GPL-3.0-only
"""Read the x86-64 Mach-O symbols and external relocations needed by the oracle.

No disassembler or platform-specific command line tools are required.
"""
import re
import struct


class MachO:
    def __init__(self, binary):
        self.binary = binary
        magic, cpu, _, _, count, command_bytes, _, _ = self.unpack('<8I', 0)
        if magic != 0xFEEDFACF or cpu != 0x01000007:
            raise ValueError('Expected a little-endian x86-64 Mach-O file')
        self.span(32, command_bytes)
        symtab = dysymtab = None
        offset = 32
        for _ in range(count):
            command, size = self.unpack('<II', offset)
            if size < 8 or offset + size > 32 + command_bytes:
                raise ValueError('Invalid Mach-O load-command size')
            if command == 2:
                if size < 24:
                    raise ValueError('Truncated symbol table command')
                symtab = self.unpack('<6I', offset)[2:]
            elif command == 11:
                if size < 80:
                    raise ValueError('Truncated dynamic symbol table command')
                dysymtab = self.unpack('<20I', offset)[2:]
            offset += size
        if offset != 32 + command_bytes or symtab is None or dysymtab is None:
            raise ValueError('Missing or inconsistent Mach-O symbol commands')
        symoff, number, stroff, string_size = symtab
        strings = self.span(stroff, string_size)
        self.span(symoff, number * 16)
        self.symbols = []
        for i in range(number):
            index, kind, section, _, address = self.unpack('<IBBHQ', symoff + 16 * i)
            if index >= string_size:
                raise ValueError('Mach-O string index outside string table')
            end = strings.find(b'\0', index)
            if end < 0:
                raise ValueError('Unterminated Mach-O symbol')
            self.symbols.append({'name': strings[index:end].decode('utf-8'),
                                 'address': address, 'type': kind, 'section': section})
        reloc_offset, reloc_count = dysymtab[14:16]
        self.span(reloc_offset, reloc_count * 8)
        self.relocations = []
        for i in range(reloc_count):
            address, bits = self.unpack('<II', reloc_offset + 8 * i)
            if address & 0x80000000:
                raise ValueError('Scattered/negative external relocation is unsupported')
            external = bool(bits & (1 << 27))
            symbol = bits & 0xFFFFFF
            if not external:
                continue
            if symbol >= len(self.symbols):
                raise ValueError('Relocation symbol index outside symbol table')
            length = 1 << ((bits >> 25) & 3)
            self.span(address, length)
            self.relocations.append({'address': address, 'symbol': self.symbols[symbol]['name'],
                                     'pcrel': bool(bits & (1 << 24)),
                                     'length': length, 'type': bits >> 28})

    def span(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.binary):
            raise ValueError('Mach-O range outside file')
        return self.binary[offset:offset + size]

    def unpack(self, fmt, offset):
        self.span(offset, struct.calcsize(fmt))
        return struct.unpack_from(fmt, self.binary, offset)

    def setters(self, class_name):
        # Mach-O's extra leading underscore precedes the Itanium C++ name.
        prefix = f'__ZN{len(class_name)}{class_name}'
        pattern = re.compile(re.escape(prefix) + r'10setParam(\d+)E8DspValue$')
        result = {}
        for symbol in self.symbols:
            match = pattern.fullmatch(symbol['name'])
            if match and symbol['section']:
                result[int(match[1])] = symbol['address']
        expected = set(range(2, 16 if class_name == 'DspFuncMozartCompressor' else 22))
        if set(result) != expected:
            raise ValueError(f'Unexpected setter symbols for {class_name}')
        return result
