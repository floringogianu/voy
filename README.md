# voy

A CLI for following arxiv authors.

## Installation

Configure an environment with `python 3.12` and run the command:

```sh
pip install git+https://github.com/floringogianu/voy.git
```

I used some experimental syntax that's only in `3.11` and above and I haven't aimed at backward compatibility yet.

## Usage

Start with `voy --help`. A general flow would be `voy search` for some researcher, `voy follow` him and then doing `voy update` after every follow or daily.
To list the most recent papers just hit `voy show`. To quickly review the papers in your feed use `voy triage`.

### Getting started

https://github.com/floringogianu/voy/assets/1670348/b86ff916-0b44-4b96-b8ea-6882d1fd4fa8

### Triage

Let's say you have a large number of researchers you follow and you want to quickly review and triage papers so that they don't show anymore in the `voy show` feed.
You can achieve this with `voy triage`.

https://github.com/floringogianu/voy/assets/1670348/6457b97f-980e-4c7d-b5f5-2f03e288554d

