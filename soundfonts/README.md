# gitignored - big binaries. See ../README.md for download URLs.

Two files belong here.

| File | Where it comes from | Size |
|---|---|---|
| `GeneralUser-GS.sf2` | download: [GeneralUser GS 2.0.3](https://github.com/mrbumpy409/GeneralUser-GS) | 31 MB |
| `OsirisUnaCorda.sf3` | **built**: `python tools\make_osiris.py --src <Osiris_Piano checkout>` | 5.8 MB |

The second is built rather than downloaded because it does not exist in this form
anywhere. [Osiris Piano](https://github.com/sfzinstruments/Osiris_Piano) (Versilian
Studios and Karoryfer Samples, CC0-1.0) ships as SFZ + FLAC, which FluidSynth cannot
load; `tools/make_osiris.py` converts it, and writes SF3 so that 206 MB of raw PCM
lands as 5.8 MB. Instructions are in the prerequisites section of `../README.md`.

Anything else dropped in here is picked up automatically - `.sf2` and `.sf3` both - and
can be named per zone in Layers. A font in `%LOCALAPPDATA%\Keys\soundfonts` shadows one
of the same name in the bundle, so a shipped font can be replaced without editing
inside the installed application.
