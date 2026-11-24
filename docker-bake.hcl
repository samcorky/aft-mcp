// docker-bake.hcl, as in menagerist: `docker buildx bake local` builds aft-mcp:local.

variable "REGISTRY" {
  default = "ghcr.io"
}

variable "OWNER" {
  default = "samcorky"
}

variable "IMAGE_NAME" {
  default = "aft-mcp"
}

variable "VERSION" {
  default = ""
}

variable "COMMIT_SHA" {
  default = ""
}

variable "BUILD_TIMESTAMP" {
  default = ""
}

variable "PLATFORMS" {
  default = "linux/amd64,linux/arm64"
}

variable "IMAGE_TAG" {
  default = "dev"
}

group "default" {
  targets = ["aft-mcp"]
}

target "aft-mcp" {
  context    = "."
  dockerfile = "Dockerfile"
  platforms  = split(",", PLATFORMS)
  tags = [
    "${REGISTRY}/${OWNER}/${IMAGE_NAME}:${IMAGE_TAG}",
    "${REGISTRY}/${OWNER}/${IMAGE_NAME}:latest",
  ]

  args = {
    VERSION                  = "${VERSION}"
    AFT_MCP_BUILD_COMMIT_SHA = "${COMMIT_SHA}"
    AFT_MCP_BUILD_TIMESTAMP  = "${BUILD_TIMESTAMP}"
  }
}

group "local" {
  targets = ["local"]
}

target "local" {
  inherits  = ["aft-mcp"]
  platforms = ["linux/amd64"]
  tags      = ["${IMAGE_NAME}:local"]
  output    = ["type=docker"]
}
