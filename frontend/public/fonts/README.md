# Vendored fonts

These WOFF2 files are self-hosted from `public/fonts`; the application does not
make runtime font requests to Google Fonts.

## IBM Plex Sans

- Upstream: <https://github.com/IBM/plex>
- Distribution: Google Fonts `ibmplexsans` v23 variable WOFF2 assets
- Weight range: 100–700, normal and italic styles
- Included subsets: Latin Extended and Latin, per style
- The italic files are the family's drawn italic (subfamily `Italic`, italic angle
  -11.31°), not a synthesized slant; the dashboard sets `font-synthesis-style: none`
  so nothing falls back to a faux oblique
- License: [SIL Open Font License 1.1](./OFL-IBM-Plex.txt)

## Alan Sans

- Upstream: <https://github.com/alan-eu/Alan-Sans>
- Distribution: Google Fonts `alansans` v7 variable WOFF2 assets
- Weight range: 300–900, normal style
- Included subsets: Arabic, Latin Extended, and Latin
- License: [SIL Open Font License 1.1](./OFL-Alan-Sans.txt)

## Atkinson Hyperlegible Mono

- Upstream: <https://github.com/googlefonts/atkinson-hyperlegible-next-mono>
- Distribution: Google Fonts `atkinsonhyperlegiblemono` v8 variable WOFF2 assets
- Weight range: 200–800, normal style
- Included subsets: Latin Extended and Latin
- License: [SIL Open Font License 1.1](./OFL-Atkinson-Hyperlegible-Mono.txt)
