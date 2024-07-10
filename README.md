# voy

A CLI for following arxiv authors.

## Installation

Configure an environment with `python 3.12` and run the command:

```sh
pip install git+https://github.com/floringogianu/voy.git
```

I used some experimental syntax that's only in `3.11` and above and I haven't aimed at backward compatibility yet.

## Usage

Start with `voy --help`. A general flow would be:
- `voy search [FirstName] LastName`: queries arxiv and returns a list of authors and their papers matching the name
- `voy follow [FirstName] LastName`: follow the author so that their new papers show up when calling `voy show`
- `voy update`: after each `voy follow` and then daily. It updates the database with the most recent papers by the authors you follow.
In addition it commits to the database every co-author on these papers.
This makes it easy to search for authors in your database, using `voy show Some Researcher`.
- `voy show` / `voy show [FirstName] LastName`: **probably the command you will use most often**. It lists the most recent papers of
the people you follow or of a specific author. Check `voy show --help` for the many options of this command.
- `voy triage`: quickly review the papers in your feed.

### Getting started

https://github.com/floringogianu/voy/assets/1670348/65a6bc6f-e813-4516-a321-76774b4f4cf7

### Triage

Let's say you have a large number of researchers you follow and you want to quickly review and triage papers so that they don't show anymore in the `voy show` feed.
You can achieve this with `voy triage`.

https://github.com/floringogianu/voy/assets/1670348/6457b97f-980e-4c7d-b5f5-2f03e288554d

