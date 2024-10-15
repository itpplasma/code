BUILD_DIR := build

.PHONY: all build test clean
all: build

build:
	mkdir -p $(BUILD_DIR)
	cd $(BUILD_DIR) && cmake ..
	cd $(BUILD_DIR) && make
	
test: build
	cd $(BUILD_DIR) && ctest

clean:
	rm -rf $(BUILD_DIR)
