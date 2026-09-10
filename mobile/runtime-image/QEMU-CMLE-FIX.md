# AArch64 host CMLE encoding backport

Limbo 6.0.1 ships QEMU 5.1.0. Its `tcg/aarch64/tcg-target.inc.c`
defines `I3617_CMLE0` as `0x2e20a800`. The correct encoding is
`0x2e209800`, as in QEMU 6.0.0 `tcg/aarch64/tcg-target.c.inc`.

Source references:
- Upstream fix: https://github.com/qemu/qemu/commit/6c2c7772f69bcd7e7a88308fd6aaf19debb7ada4
- https://github.com/qemu/qemu/blob/v5.1.0/tcg/aarch64/tcg-target.inc.c
- https://github.com/qemu/qemu/blob/v6.0.0/tcg/aarch64/tcg-target.c.inc

Equivalent source patch:

```diff
-    I3617_CMLE0     = 0x2e20a800,
+    I3617_CMLE0     = 0x2e209800,
```

Both captured Android crashes faulted on generated word `0x6ee0a800`.
Assembling `cmle v0.2d, v0.2d, #0` produces `0x6ee09800`. This is an
invalid host instruction, not a browser navigation timeout or missing tool.

`patch-qemu-cmle.py` applies this constant correction to the pinned, bundled
ARM64-host engines for both guest architectures. It verifies the complete
original SHA-256 and the opcode before writing, and accepts the identical
patched result on subsequent runs. Unknown engine versions are rejected.
The x86_64-host engines are unaffected. Keep this source-level patch with
the distributed engines and the existing Limbo/QEMU GPL notices. A future
engine upgrade should replace this backport with upstream's corrected source.

No guest CPU features, vCPU count, browser functionality, signal handling,
or timeout values are changed by this fix.
