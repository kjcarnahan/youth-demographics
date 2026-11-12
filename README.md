# Youth Demographics

Compares the teen population of South Forsyth / Johns Creek with a church youth
group, to separate real growth from growth the area simply handed you.

Needs only Python 3.9+ (no packages to install).

    python3 -m youthdemo fetch     # download data (repeat any time to update)
    python3 -m youthdemo serve     # then open http://127.0.0.1:8000

## Data sources

| Source | What | Key needed |
|---|---|---|
| NCES Common Core of Data (via Urban Institute Education Data API) | Fall enrollment by grade, every public school in Forsyth and Fulton districts, 2010 onward | No |
| Census ACS 5-year | Residents by age band (12-14, 15-17, 18-19), sex, race/ethnicity; school enrollment; community profile | Yes |
| 2020 Census (DHC table PCT12) | Population by single year of age, aged forward to 2032 | Yes |

The Census API requires a free key: https://api.census.gov/data/key_signup.html

    export CENSUS_API_KEY=yourkey
    python3 -m youthdemo fetch

## Youth-group counts

Enter them on the dashboard, or load a CSV with columns
`year,middle_school,high_school,note`:

    python3 -m youthdemo import-youth counts.csv
    python3 -m youthdemo export-youth > comparison.csv

## The growth split

With the first year you enter as the baseline:

- kept pace = baseline group size x (teens now / teens then)
- from population = kept pace - baseline
- real change = actual - kept pace

## Changing the area

Edit `config.json` (ZIP codes, city, counties, school districts, extra schools),
then run `fetch` again. Schools are also ticked on and off in the dashboard.

Data lives in `data/` (SQLite database plus a download cache). Tests:
`python3 -m unittest discover -s tests`

## Standalone web page

`python3 web/build_page.py` bakes the downloaded data into one self-contained
page, `web/dist/youth-yardstick.html`, with school and ZIP code pickers and
manual entry of youth-group counts.
