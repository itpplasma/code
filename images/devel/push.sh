#!/usr/bin/env bash
docker push ghcr.io/itpplasma/devel

docker image tag ghcr.io/itpplasma/devel registry.gitlab.tugraz.at/plasma/images/devel

docker push registry.gitlab.tugraz.at/plasma/images/devel