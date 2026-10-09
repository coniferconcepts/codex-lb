## Why
Clients need local output limits because the Codex upstream rejects `max_output_tokens`.

## What Changes
Validate a positive integer cap and continue stripping it upstream. Enforce streamed text and function arguments using `ceil(visible_output_chars / 3)`, suppress output at the cap while draining upstream through terminal usage, and return an incomplete response. Check final upstream usage as well. Truncate public text when over budget and drop oversized function calls whole. Propagate client disconnect closure through all wrappers.

## Impact
Responses HTTP request validation and output adaptation; no dependencies added.
