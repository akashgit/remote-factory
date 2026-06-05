# Build-root container — verified build environment
# JDK version is parameterized via BuildRootConfig.jdk_version
ARG JDK_VERSION=11
FROM eclipse-temurin:${JDK_VERSION}-jdk-jammy

# System packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl unzip locales findutils \
    && rm -rf /var/lib/apt/lists/*

# Deterministic locale and timezone
RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 TZ=UTC

# Gradle init scripts (repository overrides, dependency substitutions)
COPY gradle/init.d/*.gradle /root/.gradle/init.d/

# Local Maven repository (recovered artifacts)
COPY local-repo/ /root/.m2/repository/

# Project source (mounted or copied at build time)
WORKDIR /project
