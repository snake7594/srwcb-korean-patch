# SECOND `2_SCE.BIN` length-changing relocation

## Verified structure

`2_SCE.BIN` is 417,700 bytes.  Its first u32 is `0x198`, so the
file-level table contains 102 relative u32 pointers (51 scenario pairs).
For table field `i * 4`:

```text
absolute_target = field_offset + u32(field_offset)
target[2*n]     = scenario block start
target[2*n + 1] = scenario text-pool end
```

Every scenario block starts on a four-byte boundary.  Its first `0x38`
bytes are fourteen relative u32 pointers.  Each pointer is relative to its
own field.  Header field zero points to the text pool:

```text
pool_start = block_start + u32(block_start)
```

All fourteen header targets are between block-relative `0x38` and the text
pool.  When only records in the trailing pool grow, the script and all header
targets stay at the same block-relative positions, so these fourteen stored
values do not change.

The original contains 4,851 real FF-terminated records and 1,051 direct
script references to their starts:

| VM opcode | references |
| --- | ---: |
| `B1` | 823 |
| `B3` | 105 |
| `B4` | 123 |

For each of these commands, the two bytes immediately after the opcode are
an unsigned relative pointer whose base is the pointer operand itself:

```text
operand = opcode_offset + 1
record  = operand + u16(operand)
```

The remaining two bytes in the five-byte command are not part of the pointer
and must be preserved.  They include dialogue/window/portrait parameters.
An exhaustive scan found no B1/B3/B4 pointer targeting the middle of a
record.  B0, B2 and B5 do not directly target text records.

## Exact cause of the broken capture expansion

Scenario 1 starts at `0x1FD4`; its original pool is
`0x398D..0x42E9`.  The six test replacements changed record lengths by
`+26,+10,+23,+7,+6,+17` bytes.  The old script pointers were left untouched:

| record | pointer operand | stale target | required new target | error |
| ---: | ---: | ---: | ---: | ---: |
| 4 | `0x2F22` | `0x3A1B` | `0x3A1B` | 0 |
| 5 | `0x2F27` | `0x3A70` | `0x3A8A` | -26 |
| 6 | `0x2F2C` | `0x3A87` | `0x3AAB` | -36 |
| 7 | `0x2F31` | `0x3ABD` | `0x3AF8` | -59 |
| 8 | `0x2F36` | `0x3AF2` | `0x3B34` | -66 |
| 9 | `0x2F3B` | `0x3B0D` | `0x3B55` | -72 |

Thus the first replacement still works, while every later command starts in
an earlier replacement or in the middle of encoded glyph bytes.  This is why
one translated line appeared to affect the next dialogue.

The expansion was 89 bytes.  It also preserved the old three-byte gap after
the pool, moving scenario 2 from aligned `0x42EC` to unaligned `0x4345`.
The next block begins with u32 header loads, so this violates a format
invariant.  Rebuilding alignment places it at `0x4344`; the correctly rebuilt
file grows by 88 bytes in this test, even though the pool itself grows by 89.

## FF must be tokenised

Splitting a pool with `data.find(b"\xFF")` is invalid.  The renderer encoding
has double-byte glyphs and F8/F9/FB-FE controls with operands.  An operand is
allowed to equal FF.  There are 4,914 raw FF bytes in SECOND pools but only
4,851 true terminators: 63 FF bytes are glyph/control operands.

`F7` has **zero operands**.  For example, scenario-1 bytes at `0x3FC9` are
`F7 00 7D ...`: after the F7 command, `00` is a space glyph and `7D` is the
first visible glyph of the following phrase.  Treating them as two F7
operands consumes the leading ` ま` of Japanese ` まともに...`.

Token lengths are:

```text
00..EA       one-byte glyph
EB..F5 xx    two-byte glyph
F6           line advance, no operand
F7           no operand
F8 xx        one operand
F9 xx        one operand
FA           no operand
FB xx xx     two operands
FC xx xx     two operands
FD xx xx     two operands
FE xx        one operand
FF           terminator, only when reached as an opcode
```

Scenario 27 additionally has three zero tail bytes after its final true
terminator but before the odd file-table target.  They must be retained as
pool tail bytes and not treated as another record.

## Safe relocation algorithm

For every scenario independently:

1. Parse real records with the renderer token lengths above.
2. Before changing anything, scan the script (`block_start..pool_start`) for
   B1/B3/B4 commands whose u16 target equals a real record start.  Record the
   target record identity and operand's block-relative offset.
3. Keep the script length unchanged.  Rebuild the pool in the same record
   order, permitting every translated record to have a new length.  Preserve
   all controls at their semantic positions and preserve the scenario-27
   pool tail.
4. Map every original record start to its new block-relative start.
5. Rewrite each B1/B3/B4 operand as
   `new_record_relative - operand_relative`; reject a value over `0xFFFF`.
   Do not change the two following command parameter bytes.
6. Emit the script and rebuilt pool.  The odd file target is the new pool end.
7. Before emitting the next block, add zero bytes until its start is divisible
   by four.  The next even file target is this aligned start.
8. Rebuild all 102 file-table entries using
   `stored_u32 = new_absolute_target - table_field_offset`.
9. Reparse the output and require 51 aligned blocks, the same record count,
   the same 1,051 direct reference count, and every reference landing exactly
   on its intended new record start.

The reference implementation is
`korean_patch/tools/analyze_sce_relocation.py`.  An identity rebuild is
byte-for-byte identical to the original.  Rebuilding the old six-record
capture expansion produces aligned scenario 2 at `0x4344` and correctly
retargets all six commands.

## ISO constraint

The original file occupies exactly LBA 24718 through the sector before
`SECOND.WAR` at LBA 24922.  Its allocation has only 92 unused user bytes:

```text
204 * 2048 - 417700 = 92
```

Therefore a full expansion cannot use the old in-place raw-track patcher.
It requires ISO extent relocation or a rebuilt ISO layout, including both
little- and big-endian directory LBA/size fields and MODE2 Form 1 EDC/ECC.
