# The workshop design system

## Why this visual language

The sleeping sloth is the user’s desired outcome: a tool doing the tedious work while they rest. The miniature paper workshop makes a constrained repair feel tangible. Orange gives the homepage energy; forest green suggests the machine’s controls. Cream surfaces echo paper. The workspace becomes quieter so the document, evidence and approval are the focus.

The homepage composition follows the selected reference, but the functional application is a responsive interpretation, not a pixel-exact reconstruction. The hero illustration is a separate generated transparent PNG, not a screenshot used as the page.

## Tokens

| Token | Value | Role |
| --- | --- | --- |
| Apricot | #FF8739 | Hero, energetic decorative accent |
| Forest | #143B2F | Primary actions, approval, machine motif |
| Cream | #FFF7E9 | Canvas and warm background |
| Ink | #13221D | Text, dark outer frame |
| Mint | #DDE9CF | Selected states, supporting panels |
| Proof red | #B8422D | Issue annotation and error text |

Avoid small white text on orange. Buttons use cream on forest; error messages include words, not colour alone. Selected controls use aria-pressed or aria-current. Focus is a visible blue outline, distinct from decorative green borders.

## Type and spacing

Display: Rubik (weights 300-900, bundled as a woff2 subset under src/assets/fonts and served from this origin, SIL Open Font License), falling back to Arial Black and heavy Arial/Helvetica/system sans. Body: Arial/Helvetica/sans. Document: Georgia/Times New Roman. Fonts are not fetched from a CDN. Exact glyph shapes vary by operating system.

Desktop display headings: 42–83 px; mobile: about 40–58 px. Body: 13–15 px; controls: 11–13 px. Small editorial labels are supplementary rather than the only place critical information appears. Spacing follows a loose 4/8 px rhythm, with generous 24–48 px page gutters. Main buttons have at least 50 px height. Cards use 8–16 px radii; capsule shapes belong to status and navigation.

## Layout

- Home: dark frame, orange hero, asymmetric copy/art split, cream three-step strip.
- Upload: primary dropzone plus sample invitation; single column on phones.
- Workspace: outline / paper / inspector. Outline becomes horizontal on tablets; inspector stacks below the paper on phones.
- Review: two sheets on desktop, vertical sheets on phones; Original and Proposed tabs also offer one-sheet viewing. Sticky approval bar stays separate from paper content.
- Results: centred receipt; history: practical document rows rather than unrelated dashboard charts.

CSS breakpoints: 1100, 850 and 600 px; 1550 px adds breathing room on wide monitors. No screen is implemented as a flattened mockup image.

## Motion and state language

A 7-second float is limited to the homepage artwork. Controls use short feedback. System prefers-reduced-motion overrides animation; Settings also disables it for the session. Loading must always include readable status and a cancel control.

“Proposal ready” means reviewable, not accepted. “Accepted” requires an explicit action. Unsupported demo requests lead to clarification. Real backend failures must display the backend’s actual status; do not disguise malformed model output as a clarifying question.

## Assets

public/assets/workshop.png was generated for this design from the supplied workshop concept. Lucide icons retain their upstream ISC licensing in dependencies. System fonts are not redistributed. No external stock photos or remote assets are required. See #design in the running app for the living component sheet.
