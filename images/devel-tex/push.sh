#!/usr/bin/env bash
docker push ghcr.io/itpplasma/devel-tex

docker image ghcr.io/itpplasma/devel-tex registry.gitlab.tugraz.at/plasma/images/devel-tex

docker push registry.gitlab.tugraz.at/plasma/images/devel-tex