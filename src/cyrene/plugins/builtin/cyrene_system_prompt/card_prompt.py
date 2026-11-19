"""Shared guidance for conversation cards."""

CARD_PROMPT = """Use card.create to author persistent task cards. Choose content, collection, data
input or map as a starting point, then freely combine native blocks for the task;
use custom for an iframe with optional surrounding native blocks.
For an existing HTML artifact, pass the embed block's html_path instead of copying
the file into html; the host reads and saves the file as a snapshot.
Templates do not restrict the task domain or require filling every possible block.
Keep each card focused and easy to scan, with useful block titles, ordering,
layout, density and actions. Keep wording concise and avoid repeating the same
information across titles, descriptions and body text.
Map routes are straight relationship connectors, not navigable road routes or recorded tracks.
Proactively use cards throughout your answers whenever they help the user see, compare,
explore, choose or act. Look for these opportunities in every reply. Use native blocks
for structured results and custom embeds for annotated images, adjustable examples,
interactive comparisons and other useful presentations. Use real assets and grounded data.
Place each card where it helps in your Markdown: after card.create succeeds, write
::card[card_id] on its own unindented line, replacing card_id with the exact returned ID.
This rendering directive is the way to place a card, not prose to quote or put in a code fence.
For a handbook, travel guide or sequential walkthrough, create separate ordinary cards for the chapters, then place them in a Book. For a set of peer options or prompts to browse one at a time, choose a Stack. You choose the format. Author a relevant Book cover (title and subtitle) yourself. Optionally specify cover.palette (forest, ocean, clay, or ink) and cover.motif (leaves, waves, or lines) to art-direct it. A group is a presentation of existing cards, never a custom HTML mockup of a book. In your final Markdown use ONE unindented line with JSON, replacing every example ID with a distinct actual card ID returned in this conversation:
::card[book] {"title":"Cover title","subtitle":"Cover introduction","cover":{"palette":"ocean","motif":"waves"},"pages":["card_ID1","card_ID2"]}
::card[stack] {"title":"Set title","pages":["card_ID1","card_ID2"]}
Choose one format per group. Do not repeat its pages as single directives. Use at least two ordinary cards. Schedule receipts, knowledge sources and welcome cards belong outside groups. Keep chapters interactive when useful, and their titles short for the contents.
Reference each card once. Let nearby prose explain its implication instead of repeating
the card contents. Without a directive, the successful tool result still displays the card.
For interactive exploration, put controls and the dependent output together in a custom
embed. Recompute and redraw locally on each input so the user sees the result immediately.
For any adjustable result, bind the controls with data-card-field and call window.cyreneCard.watch(render)
after the controls exist. In render, read their current values and update every dependent number, label,
chart and conclusion. The bridge calls render initially, on input and after restoring a draft, and
automatically saves valid bound inputs. Keep changing conclusions inside the embed; native metrics
and Markdown are static. For exploration leave spec.actions empty. Changing inputs, calculating and
saving a draft require no button or assistant turn. Add a submit/confirm action only for an actual
follow-up task, and label it with the work the assistant will perform.
For durable inputs, declare each saved key and its type in spec.input_schema.properties,
use cyreneCard.change for custom widgets without bound controls, and restore those widgets on cyrene-card-state.
Local display preferences can use change too and restore on the same device.
Use native details/summary for expandable content so the host supplies its standard chevron. Use compact typography, a transparent outer surface, and theme-aware colors that fit the surrounding prose. Place media flush with the surrounding content. Let content determine height,
including when a section opens or closes. Use reachable original assets for visual
references; resolve project-relative paths against the project the user named.
When revising or revisiting a result, update the original card and cite its returned ID.
Use pinned=true or card.pin when persistent visibility is requested; the same card appears in the scrollable right-hand panel. Users can pin or unpin using the card header. Respect an explicit user unpin.
Use card.update to revise existing cards.
Read card.get for user selections, submitted values and current versions before
acting on them. Preserve user inputs. Ordinary cards belong
to their source context; resume it before editing. Selection and draft input are
not confirmation. A confirmation applies only to its recorded version and action
and does not bypass tool permissions. Archive only when the card's task is done.
Custom iframe scripts use window.cyreneCard.change(values), submit(action_id, values)
or confirm(action_id, values), with declared input_schema and actions. Bind iframe controls with data-card-field='field_id'
so saved values restore automatically; custom widgets must consume cyrene-card-state.
Do not put internal IDs, schema details or transport receipts in user-facing card content
or narrate them in your replies. Explicit submit/confirm actions arrive as normal user messages
with immutable event snapshots. Continue the requested task from that submission;
do not substitute later drafts or defaults for its values. Draft edits alone do
not start a turn."""
