# SECOND battle/death message archive structure

This note describes the original files shipped in the current Complete Box
disc image.  The executable evidence is from `SECOND/SECOND.WAR`.

## Verified originals

| File | Size | SHA-256 |
|---|---:|---|
| `BMESS2.BIN` | 619,699 | `4624bcdea98bbaad1a770c8820cb7c93f8e6d346907f958f45d727664e373a8a` |
| `SECOND/2_DEAD.BIN` | 4,450 | `87f13ee163611f9e3fad0f5b7664bfd6dab72f66d212ba013912be4bc136c308` |

The executable's archive reader is at `0x80058664`.  Every stored u32 pointer
is relative to its own field: `absolute = field_offset + stored_u32`.

## BMESS2.BIN

### Outer archive

- Pointer table: `0x640` bytes, 400 relative u32 entries.
- Entries 0..398: starts of 399 CPE blocks.
- Entry 399: EOF boundary, exactly `0x974B3` in the original.
- CPE blocks are not aligned.  Original starts and lengths occur at all modulo-4
  residues, so a rebuilder must not add guessed padding.

Each interval has this exact CPE wrapper:

```text
43 50 45 01       CPE\x01
08 00             select unit 0
01                load-data chunk
00 00 00 00       payload load address (zero / relative)
ss ss ss ss       little-endian payload size
...payload...
00                CPE EOF command
```

Thus `CPE interval size = payload size + 16`.

### Payload graph

- Payload offsets `0x00..0x13`: ten self-relative u16 dispatch pointers.
- From `0x14`: two selector lists of six-byte entries
  `(u16 selector_id, u32 payload_offset)`.  Each list includes a terminal entry
  whose selector id is `0xFFFF`.
- The end of the second selector list equals the smallest dispatch target in
  every one of the 399 original blocks.
- Graph nodes include arrays (`type 0x000D`), condition wrappers, and leaves.

Only a leaf directly points to display text:

```text
+0  u16  type (0x0010 or 0x0011)
+2  u16  context field -- preserve
+4  u16  voice/variant field -- preserve
+6  u32  absolute offset from payload start to quoted message
```

A leaf target is valid only when token-aware parsing produces a complete
renderer record whose first and last glyph ids are `0x03E` (`「`) and `0x03F`
(`」`) and whose terminator is `0xFF`.  A node signature inside a text range is
not a node.

Original invariants:

- 19,913 leaf references.
- 17,364 block-local unique referenced strings.
- 4,764 unique Japanese strings after cross-block deduplication.
- 54 syntactically valid quoted strings have no leaf reference and are not
  patch/reclamation targets.
- One deliberate overlap exists in block 190: the record at payload `0x342`
  contains the suffix record at `0x347`; both are referenced.
- Referenced text controls consist only of 108 `0xF6` line advances.

### Runtime size limit

The archive reader uses the `0x2000` flag in file id `0x201A` to select a
four-byte pointer stride.  Battle setup reads CPE blocks to:

| Call | Destination |
|---|---:|
| `0x800D6A60` | `0x801D0000` |
| `0x800D6ADC` | `0x801D3000` |
| `0x800D6B14` | `0x801D6000` |
| `0x800D6B4C` | `0x801D9000` |

These are four adjacent `0x3000`-byte runtime slots.  The complete rebuilt CPE
interval, including its 16-byte wrapper, must not exceed `0x3000`.

### Safe expansion algorithm

1. Parse only leaf-referenced renderer records; do not use flat FF splitting.
2. For all selected records in one block, form the union of their source byte
   ranges.  These ranges are reclaimable holes.  Do not reclaim the 54
   unreferenced strings or unselected strings.
3. Fill reclaimed holes, then best-fit expanded translations into them.
4. Append only records that do not fit in a hole.
5. Rewrite every leaf `+6` field that shared an old target to the selected new
   target.  Leave dispatch, selector, context, voice, and wrapper bytes intact.
6. Reject the block if wrapper plus payload exceeds `0x3000`.
7. Rewrite its CPE payload size, concatenate all blocks without alignment, and
   rewrite all 400 outer relative pointers including the EOF entry.
8. Relocate/resize the ISO file; do not overwrite the following extent.

`tools/analyze_second_message_archives.py::rebuild_bmess_repack` implements this
algorithm.  A synthetic build that nearly doubled every message's content had
a maximum CPE size of `0x19A2`, below the runtime limit.

## SECOND/2_DEAD.BIN

- Pointer table: `0x7FC` bytes, 511 relative u32 entries.
- The executable uses file id `0x14` without the `0x2000` flag, so archive index
  selection uses `index << 3`.
- Entries `(2*i, 2*i+1)` are a start/end pair for slot `i`.
- Entries 0..509 describe 255 slots: 95 nonempty records and 160 empty records.
- Entry 510 is the final live-data boundary at original offset `0x1157`.
- Bytes `0x1157..0x1162` (11 bytes) form a valid-looking renderer record but no
  start/end pair references it.  Preserve it as trailing data; do not translate
  it as a live death quote.
- Of the 95 live records, 93 contain a 2..5-glyph speaker-name prefix followed
  by `「...」`; two are unquoted special records.  Speaker prefixes are part of
  the record and must not be removed.
- Live text controls consist only of 18 `0xF6` line advances.

Safe rebuilding walks slots in order, emits an expanded record only for a
nonempty pair, writes that slot's new start and end, writes identical start/end
for empty slots, sets entry 510 to the new live-data end, and then appends the
11 original trailing bytes unchanged.

## Parser hazards

- `0xEB..0xF5` introduce a two-byte glyph.  Its second byte may itself be
  `0xFF`; `find(FF)` can therefore split a glyph rather than find a terminator.
- `0xF6..0xFE` are controls with opcode-specific argument lengths.  Only `F6`
  occurs in the canonical live BMESS2/2_DEAD text, but the parser must still
  understand all opcodes to reject malformed boundaries.
- `0x00` is the blank glyph, not structural padding inside a live record.
- CPE headers, selector lists, graph arrays, leaf metadata, and outer pointers
  contain byte patterns that resemble glyphs.  Scanning a whole CPE interval as
  text corrupts portraits/variants and eventually crashes battle startup.
