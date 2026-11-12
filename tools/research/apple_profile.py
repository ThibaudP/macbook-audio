# SPDX-License-Identifier: GPL-3.0-only
"""Decode the pinned layout-59/profile-0 data and its static EQ arithmetic."""
import hashlib
import math
import plistlib
import struct
import zlib

DSP_SHA256 = '07abb2dbf7968fd6f46e0242edb00d752503b92815474a163a7603d7066863de'
LAYOUT_SHA256 = '5ed766c138e65d4231a60086317ed4b8bef1b212c0d5b59201f98387cae89c26'
LIMIT = 16 * 1024 * 1024
NAMES = {0: 'lowpass', 1: 'highpass', 2: 'bandpass', 3: 'notch',
         4: 'peaking', 5: 'low_shelf', 6: 'high_shelf'}


def f32(value):
    return struct.unpack('<f', struct.pack('<I', value & 0xFFFFFFFF))[0]


def coefficients(kind, frequency, apple_q, gain_db, sample_rate=48000, bypass=False):
    if bypass or frequency > sample_rate * .49:
        return [1., 0., 0., 1., 0., 0.]
    omega = 2 * math.pi * frequency / sample_rate
    cosine, sine = math.cos(omega), math.sin(omega)
    alpha = sine * math.sinh(1 / (2 * apple_q))
    a = [1 + alpha, -2 * cosine, 1 - alpha]
    if kind == 0:
        b = [(1 - cosine) / 2, 1 - cosine, (1 - cosine) / 2]
    elif kind == 1:
        b = [(1 + cosine) / 2, -(1 + cosine), (1 + cosine) / 2]
    elif kind == 2:
        b = [alpha, 0, -alpha]
    elif kind == 3:
        b = [1, -2 * cosine, 1]
    elif kind == 4:
        amplitude = 10 ** (gain_db / 40)
        b = [1 + alpha * amplitude, -2 * cosine, 1 - alpha * amplitude]
        a = [1 + alpha / amplitude, -2 * cosine, 1 - alpha / amplitude]
    elif kind in (5, 6):
        amplitude = 10 ** (gain_db / 40)
        slope = 0.7071067811865475
        beta = sine * math.sqrt((amplitude ** 2 + 1) / slope - (amplitude - 1) ** 2)
        plus, minus = amplitude + 1, amplitude - 1
        if kind == 5:
            b = [amplitude * (plus - minus * cosine + beta),
                 2 * amplitude * (minus - plus * cosine),
                 amplitude * (plus - minus * cosine - beta)]
            a = [plus + minus * cosine + beta, -2 * (minus + plus * cosine),
                 plus + minus * cosine - beta]
        else:
            b = [amplitude * (plus + minus * cosine + beta),
                 -2 * amplitude * (minus + plus * cosine),
                 amplitude * (plus + minus * cosine - beta)]
            a = [plus - minus * cosine + beta, 2 * (minus - plus * cosine),
                 plus - minus * cosine - beta]
    else:
        raise ValueError(f'Unsupported filter type {kind}')
    return [*(x / a[0] for x in b), 1., a[1] / a[0], a[2] / a[0]]


def read_layout(original):
    if len(original) > LIMIT:
        raise ValueError('Layout exceeds 16 MiB')
    raw = original
    if not raw.lstrip().startswith((b'<?xml', b'<dict', b'<plist', b'bplist')):
        for offset in (0, 8):
            try:
                decompressor = zlib.decompressobj()
                raw = decompressor.decompress(original[offset:], LIMIT + 1)
                if len(raw) > LIMIT or not decompressor.eof:
                    raise ValueError('Truncated or oversized compressed layout')
                break
            except zlib.error:
                continue
        else:
            raise ValueError('Unrecognized layout stream')
    if hashlib.sha256(raw).hexdigest() != LAYOUT_SHA256:
        raise ValueError('Unsupported layout59 resource fingerprint')
    if raw.lstrip().startswith(b'<dict'):
        raw = b'<plist version="1.0">' + raw + b'</plist>'
    layout = plistlib.loads(raw)
    if layout['LayoutID'] != 59:
        raise ValueError('Expected layout 59')
    return layout


def select_profile(layout):
    speaker = layout['PathMapRef'][0]['IntSpeaker']
    functions = []
    for name, source in speaker['SignalProcessing'][0]['SoftwareDSP'].items():
        info = source['FunctionInfo']
        entry = {'function': name, 'name': info['DspFuncName'],
                 'instance': info['DspFuncInstance'], 'inputs': source.get('PatchbayInfo', {}),
                 'raw_parameters': source.get('ParameterInfo', {})}
        if info['DspFuncName'] == 'DspEqualization32':
            filters, slots = [], set()
            for raw in source['ParameterInfo']['Filter']:
                channel, band = raw['2'], raw['3']
                if channel not in (0, 1) or (channel, band) in slots:
                    raise ValueError('Unexpected EQ channel or repeated slot')
                slots.add((channel, band))
                kind, frequency, q, gain = raw['5'], f32(raw['6']), f32(raw['7']), f32(raw['8'])
                filters.append({'channel': channel, 'band': band, 'bypass': bool(raw['4']),
                                'type': kind, 'kind': NAMES[kind], 'frequency_hz': frequency,
                                'apple_q': q, 'gain_db': gain, 'raw': raw,
                                'sos_48000': coefficients(kind, frequency, q, gain, bypass=bool(raw['4']))})
            entry['filters'] = sorted(filters, key=lambda f: (f['channel'], f['band']))
        functions.append(entry)
    functions.sort(key=lambda f: f['instance'])
    if [f['instance'] for f in functions] != list(range(14)):
        raise ValueError('Expected the complete fourteen-stage graph')
    return {'sample_rate': 48000, 'source': {'dsp_binary_sha256': DSP_SHA256,
             'layout_uncompressed_sha256': LAYOUT_SHA256},
            'variant': {'index': 0, 'functions': functions}}
