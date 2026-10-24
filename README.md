# hackachusetts-2026

Our project for Hackachusetts 2026.

## The Team

- Austin
- A.J.
- Adam

## What is Hackachusetts?

Hackachusetts is a statewide AI hackathon in Massachusetts, run across multiple
campuses at the same time. Students team up over a weekend, pick a problem, and
build something that works by the deadline.

More info: [Hackachusetts via UMass CICS](https://www.cics.umass.edu/events/hackachusetts)

## The Project: Metropolis

Metropolis is an EMS destination-routing platform. It takes the hospital choice off the dispatcher's gut: it filters hospitals by what they can treat, ranks them by minutes to definitive care (drive time + predicted ED wait), and suggests destination swaps between ambulances when that gets the sickest patients to care faster.

Code, setup and testing instructions: [`metropolis/README.md`](metropolis/README.md).

## Getting Started

```bash
git clone https://github.com/aust1n2170/hackachusetts-2026.git
cd hackachusetts-2026/metropolis
cp .env.example .env
docker compose up --build    # console at http://localhost:5173
```
