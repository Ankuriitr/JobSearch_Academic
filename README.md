# Academic Job Openings India

A tiled directory of faculty openings at IITs and IISc, NITs, IISERs and central universities,
with a scraper that keeps the links, status and last date to apply up to date.

## What is in the folder

```
academic-jobs-site/
  index.html                  the page
  css/styles.css              styling; colours are at the top
  js/app.js                   filtering, sorting, drawing the tiles
  data/listings.json          THE DATABASE: every listing and job profile (edit this one)
  data/listings.js            generated copy the page loads (do not edit)
  data/scrape-report.md       written by each scraper run: what changed, what needs review
  scraper/scrape.py           the scraper
  scraper/test_scrape.py      offline tests for the scraper's rules
  scraper/requirements.txt    Python libraries it needs
  .github/workflows/update-listings.yml   runs the scraper daily on GitHub
```

Open the site on your computer by double-clicking `index.html`.

## How the scraper works

For each listing, `scraper/scrape.py`:

1. **Finds the recruitment page.** It opens the saved apply link. If there is none, or it is dead, it starts
   at the institute homepage and follows the links that look like faculty recruitment, two levels deep.
2. **Finds the advertisement.** On that page it scores every link (words like faculty, professor, rolling,
   advertisement count for it; non-teaching, JRF, project, tender count against it) and opens the best ones,
   including PDFs.
3. **Reads the last date.** It looks for a date right after phrases such as "last date", "on or before",
   "closing date", "extended up to", and for rolling-advertisement wording.
4. **Verifies.** The page must load, be on the institute's own domain, and be about faculty posts.
5. **Decides.**
   - High confidence (a last date or rolling wording was read): the listing is updated automatically
     with status, last date, advertisement link and today's date as "checked".
   - Medium (page found, but no date could be read, or the PDF is a scan): links are filled in,
     status is left alone, and the listing goes under "Needs your review" in the report.
   - Low (site down or nothing found): nothing changes; it is listed under "Could not be read".
   - A listing whose last date has passed is closed automatically.
   - It never flips an Open listing to Closed only because it found an older advertisement; that goes to review.

Set `"locked": true` on a listing to make the scraper leave it alone.

### Run it yourself

```
pip install -r scraper/requirements.txt
python scraper/scrape.py --only "IIT Madras" --dry-run    # try one, change nothing
python scraper/scrape.py --kind NIT                       # one institute type
python scraper/scrape.py                                  # everything (about 10 to 20 minutes)
python scraper/test_scrape.py                             # offline tests
```

Then read `data/scrape-report.md`.

### Run it automatically (no manual work)

Put the folder in a GitHub repository with GitHub Pages turned on (see Hosting). The included workflow then:

- runs the scraper every day at 06:47 India time and commits the changes, so the live site updates itself;
- rebuilds `data/listings.js` whenever you edit `data/listings.json` on github.com;
- can be started any time from the repository's Actions tab ("Update listings", "Run workflow").

One-time setting: repository Settings, Actions, General, Workflow permissions, choose "Read and write permissions".

### Optional add-ons (each needs a paid API key, stored under Settings, Secrets and variables, Actions)

- `BRAVE_API_KEY`: lets the scraper search the web when it cannot find a recruitment page from the homepage.
  Google no longer offers a search API to new customers, so the scraper uses Brave Search instead.
- `ANTHROPIC_API_KEY`: has Claude read each advertisement as a second opinion. When its answer and the rule-based
  reading disagree, the listing goes to review instead of being updated. This also reads dates that are written
  in unusual ways.

Without these keys the scraper still works; it just sends more listings to review.

### Known limits

- Scanned PDFs (pictures of text) cannot be read; those go to review.
- Pages built entirely by JavaScript, or portals behind a login, show the scraper no text.
- Some institute sites are slow, have broken security certificates, or block visitors from outside India;
  GitHub's servers are outside India, so a few sites may fail there but work from your own computer.
- The reading rules are heuristics. Expect to tune the word lists at the top of `scrape.py` after the first runs.

## Edit a listing by hand

Edit `data/listings.json` (one line per listing), then run `python scraper/scrape.py --build-only`.
On GitHub the rebuild happens by itself after you commit.

Fields: `profile`, `kind` (IIT, IISc, NIT, IISER, CU), `name`, `city`, `state`,
`status` (active, inactive, unverified), `deadline` (YYYY-MM-DD or empty), `rolling` (true/false),
`applyUrl`, `adUrl`, `siteUrl`, `note`, `checked` (YYYY-MM-DD), `confidence` (set by the scraper), `locked`.

Add a job profile by adding to `"profiles"` at the top of the same file, then listings with that profile id.
Add an institute type in `GROUPS` and `KIND_LABEL` at the top of `js/app.js`.

## Hosting (free)

### GitHub Pages (needed for the automatic updates)
1. Create a free account at https://github.com and a new public repository.
2. Upload the contents of this folder, keeping `index.html` at the top level. Include the hidden `.github` folder;
   if drag-and-drop skips it, create `.github/workflows/update-listings.yml` with "Add file, Create new file" and paste its contents.
3. Settings, Pages, "Deploy from a branch", branch `main`, folder `/ (root)`, Save.
4. Settings, Actions, General, Workflow permissions, "Read and write permissions", Save.
5. The site appears at `https://<your-username>.github.io/<repository>/`.

### Netlify Drop (quickest, but no automatic updates)
Drag the folder onto https://app.netlify.com/drop.

### Your own domain
Add it under Settings, Pages, Custom domain. HTTPS is free.

## Data status

IIT apply links come from the IIT Council listing, checked 14 Nov 2026. Homepage links for IISc, NITs, IISERs and
central universities were written from memory and are unchecked. The scraper has not yet been run against the live sites.
