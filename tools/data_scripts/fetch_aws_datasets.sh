#!/bin/bash
# Copyright (c) Facebook, Inc. and its affiliates.

ROOTDIR=$(readlink -f $(dirname "$0")/../../)
echo "$ROOTDIR"
DATA_DIRNAME=datasets_folder

if [ -z $1 ]
then
	AGENT="craftassist"
else
	AGENT=$1
fi

cd $ROOTDIR

echo "====== Downloading datasets to $ROOTDIR/$DATA_DIRNAME.tar.gz ======"
curl http://craftassist.s3-us-west-2.amazonaws.com/pubr/$DATA_DIRNAME.tar.gz -o $DATA_DIRNAME.tar.gz

if [ -d "${AGENT}/agent/datasets" ]
then
	echo "Overwriting datasets directory"
	rm -r $AGENT/agent/datasets/
fi
mkdir -p $AGENT/agent/datasets/

tar -xzvf $DATA_DIRNAME.tar.gz -C $AGENT/agent/datasets/ --strip-components 1
