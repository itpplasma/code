# base image (fundamental installations)
FROM ghcr.io/itpplasma/devel

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# TexLive
RUN apt-get update && apt-get install -y -q --no-install-recommends \
		texlive-latex-base \
		texlive-latex-recommended \
		texlive-publishers \
		texlive-science \
		texlive-bibtex-extra \
		texlive-luatex \
		latexmk \
		biber \
		ghostscript \
		lyx \
		doxygen-latex \
		&& \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# TexLive Fonts
RUN apt-get update && apt-get install -y -q --no-install-recommends \
		texlive-fonts-recommended \
		fonts-cmu \
		fonts-inconsolata \
		fonts-linuxlibertine \
		fonts-lmodern \
		fonts-texgyre \
		fonts-texgyre-math \
		&& \
	fc-cache -f -v && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Entry point (bash)
CMD ["/bin/bash"]
