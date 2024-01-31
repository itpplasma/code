#!/usr/bin/env bash
docker push ghcr.io/itpplasma/base

docker image tag ghcr.io/itpplasma/base registry.gitlab.tugraz.at/plasma/images/base

docker push registry.gitlab.tugraz.at/plasma/images/base