---
version: alpha
name: mircrewrr admin
description: Admin panel of a Torznab proxy. It answers "is the proxy working, and where does a search break", then lets one person see each indexer, change its settings, change the configuration and read the live log.
colors:
  background: "#101426"
  surface: "#171c33"
  surface-raised: "#1c2240"
  border: "#2a3152"
  foreground: "#e6e9f5"
  foreground-muted: "#9aa3c7"
  ok: "#5be0d0"
  warning: "#ffb547"
  danger: "#ff6b6b"
  danger-surface: "#2a1a24"
  primary: "#8c7cff"
  primary-foreground: "#101426"
typography:
  sans:
    fontFamily: Schibsted Grotesk
  title:
    fontFamily: Schibsted Grotesk
    fontSize: 1.75rem
    lineHeight: 1.15
    fontWeight: 700
    letterSpacing: -0.02em
  heading:
    fontFamily: Schibsted Grotesk
    fontSize: 1.125rem
    lineHeight: 1.3
    fontWeight: 600
  body:
    fontFamily: Schibsted Grotesk
    fontSize: 0.9375rem
    lineHeight: 1.5
    fontWeight: 400
  small:
    fontFamily: Schibsted Grotesk
    fontSize: 0.8125rem
    lineHeight: 1.45
    fontWeight: 500
  mono:
    fontFamily: ui-monospace
    fontSize: 0.75rem
    lineHeight: 1.7
rounded:
  base: 12px
  small: 8px
  pill: 999px
spacing:
  base: 4px
  gap: 16px
  section: 32px
---

## Overview

The panel is read far more often than it is edited. Opening it must answer one question without scrolling: can the proxy reach the forum, and if not, at which step does it fail. The Dashboard therefore shows the path of a search (callers, proxy, Cloudflare solver, forum session) as the main object, with the broken step marked and its fix next to it. Editing screens (Indexers, Configuration) and the log keep the same language and add no new patterns. Indexers shows one card per indexer, plugin or external service alike, with a Settings button, and below the cards the plugin folders found, each marked loaded or not loaded with the reason.

## Colors

The default theme is dark; the light theme follows the system setting and the panel offers a manual override (system, light, dark), remembered in the browser.

`ok`, `warning` and `danger` mark state and nothing else; every state also carries an icon and a word, never colour alone. `primary` is the only fill for a primary control. Text on a state or primary fill uses the foreground token made for that fill, not white by habit. Secondary text uses `foreground-muted`; on a tinted state surface it takes that state's hue, not grey.

## Themes

| Token | Light |
|---|---|
| background | #f4f5fa |
| surface | #ffffff |
| surface-raised | #eef0fa |
| border | #d5d9ea |
| foreground | #12162a |
| foreground-muted | #4f5878 |
| ok | #0b7a6b |
| warning | #8a5a00 |
| danger | #c0262d |
| danger-surface | #fdeceb |
| primary | #5a45e0 |
| primary-foreground | #ffffff |

## Typography

One family carries the whole panel. Weight and size, not colour, separate title from heading from body. Monospace is for the log, identifiers and URLs only. Numbers that must line up (log times) sit in the monospace face; elsewhere figures are proportional. Body text stays under 75 characters per line.

## Layout

A fixed side navigation on wide screens collapses to a top bar below 768px; content never scrolls sideways except inside a table or code box. The Dashboard has two views chosen by a switch at the top of the page, Detailed and Simple, remembered in the browser. Detailed shows the path of a search per site, the services and the live log. Simple shows one verdict for the whole proxy, one large tile per site and the actions that fix a problem, and nothing else. Both views show the same states and offer the same actions.

## Elevation & Depth

Depth comes from the surface tokens and one 1px border; no shadow sits under a bordered surface. Only floating layers (dialogs, toasts) carry a soft offset shadow.

## Shapes

Cards and dialogs use `rounded.base`, controls use `rounded.small`, and only small status chips are pills. Cards never nest.

## Components

A status chip is an icon, a word and a state tint. A site's path is drawn from separate placed boxes joined by lines, one lane per site; the line and box of a broken step take the danger tone and a dashed line, and a short sentence under the lane names the step and offers the fix. The log shows level as a word. Motion is limited to one authored moment: the boxes of the path light up in sequence when the Dashboard first loads. With reduced motion everything is shown at once, still.

## Do's and Don'ts

- Do keep a visible focus ring on every control in both themes.
- Do name controls by their action ("Retry login", "Save changes") and keep one name for one action across the flow.
- Don't use a glyph or emoji as an icon; icons are drawn in one stroke weight.
- Don't put a kicker or label above a heading.
- Don't mark state with colour alone.
