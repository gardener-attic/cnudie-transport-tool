# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

FROM eu.gcr.io/gardener-project/cc/job-image-test:1.1780.0

COPY . /cnudie-transport-tool

RUN pip3 install /cnudie-transport-tool
