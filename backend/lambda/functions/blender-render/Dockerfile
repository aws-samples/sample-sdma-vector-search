# Blender Lambda Container Image
# Based on the AWS Lambda Python 3.12 base image with Blender 4.0 installed
#
# This container renders a single view of a 3D model using Blender Cycles.
# It's designed to be invoked in parallel (8 instances for 8 views).

# Use Python 3.12 which is based on Amazon Linux 2023 (has GLIBC 2.34)
# Blender 4.0 requires GLIBC 2.27+
FROM public.ecr.aws/lambda/python:3.12

# Install system dependencies for Blender headless rendering
# Blender requires many X11 and graphics libraries even in headless mode
RUN dnf install -y \
    tar \
    xz \
    libGL \
    libGLU \
    libX11 \
    libXext \
    libXrender \
    libXi \
    libXxf86vm \
    libxkbcommon \
    libSM \
    libICE \
    libXfixes \
    libXcursor \
    libXrandr \
    libXinerama \
    mesa-libGL \
    mesa-dri-drivers \
    alsa-lib \
    pulseaudio-libs \
    && dnf clean all

# Download and install Blender 4.0 (headless build)
# The checksum is the one Blender publishes alongside the release
# (https://download.blender.org/release/Blender4.0/blender-4.0.2.sha256).
# Verifying it means a tampered or truncated download fails the build instead of
# producing an image that renders wrongly or not at all.
ARG BLENDER_VERSION=4.0.2
ARG BLENDER_SHA256=5583a5588736da8858c522ef17fff5d73be59c47a6fe91ad29c6f3263e22086a
RUN curl -fL "https://download.blender.org/release/Blender4.0/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
    -o /tmp/blender.tar.xz \
    && echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/blender.tar.xz -C /opt/ \
    && rm /tmp/blender.tar.xz \
    && ln -s "/opt/blender-${BLENDER_VERSION}-linux-x64/blender" /usr/local/bin/blender

# Set environment variables for headless rendering
ENV BLENDER_USER_SCRIPTS=/opt/blender-${BLENDER_VERSION}-linux-x64/scripts
ENV XDG_RUNTIME_DIR=/tmp/runtime
ENV DISPLAY=

# Create runtime directory
RUN mkdir -p /tmp/runtime

# Copy Python dependencies
COPY functions/blender-render/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy Blender render scripts
COPY functions/blender-render/blender_scripts/ ${LAMBDA_TASK_ROOT}/blender_scripts/

# Copy the Lambda handler and its function-local modules.
COPY functions/blender-render/handler.py \
     functions/blender-render/system_defaults.py \
     ${LAMBDA_TASK_ROOT}/

# Copy the shared modules straight from their source. The build context is
# backend/lambda/ (see scripts/lib/build-image.sh) precisely so that this is
# possible; previously these files had to be copied into the function
# directory by deploy.sh before the image could build.
COPY shared/rendering_constants.py shared/common_constants.py shared/log_utils.py ${LAMBDA_TASK_ROOT}/

# Set the handler
CMD ["handler.lambda_handler"]
