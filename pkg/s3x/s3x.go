// Package s3x gives the Go services S3 access that works unchanged on MinIO
// and AWS. Two clients: the internal one talks to the storage service, the
// public one only signs URLs, with the host the BROWSER can reach (a
// signature covers the host, so it cannot be rewritten afterwards).
package s3x

import (
	"context"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

type Store struct {
	Bucket   string
	internal *minio.Client
	public   *minio.Client
}

func newClient(endpoint, region string, pathStyle bool) (*minio.Client, error) {
	secure := true
	host := "s3." + region + ".amazonaws.com"
	if endpoint != "" {
		u, err := url.Parse(endpoint)
		if err != nil {
			return nil, err
		}
		secure = u.Scheme == "https"
		host = u.Host
	}
	lookup := minio.BucketLookupAuto
	if pathStyle {
		lookup = minio.BucketLookupPath
	}
	creds := credentials.NewChainCredentials([]credentials.Provider{
		&credentials.EnvAWS{}, &credentials.EnvMinio{}, &credentials.IAM{},
	})
	return minio.New(host, &minio.Options{Creds: creds, Secure: secure, Region: region, BucketLookup: lookup})
}

func New(bucket, endpoint, publicEndpoint, region string, pathStyle bool) (*Store, error) {
	for _, k := range []string{"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"} {
		if v, ok := os.LookupEnv(k); ok && v == "" {
			os.Unsetenv(k) // empty = not set: fall through to the instance role
		}
	}
	in, err := newClient(endpoint, region, pathStyle)
	if err != nil {
		return nil, err
	}
	pub := in
	if publicEndpoint != "" && publicEndpoint != endpoint {
		if pub, err = newClient(publicEndpoint, region, pathStyle); err != nil {
			return nil, err
		}
	}
	return &Store{Bucket: bucket, internal: in, public: pub}, nil
}

// Presign returns a GET URL for key, valid for ttl, signed for the public host.
func (s *Store) Presign(ctx context.Context, key string, ttl time.Duration, filename string) (string, error) {
	params := url.Values{}
	if filename != "" {
		params.Set("response-content-disposition", `attachment; filename="`+strings.ReplaceAll(filename, `"`, "")+`"`)
	}
	u, err := s.public.PresignedGetObject(ctx, s.Bucket, key, ttl, params)
	if err != nil {
		return "", err
	}
	return u.String(), nil
}

func (s *Store) Ping(ctx context.Context) error {
	_, err := s.internal.BucketExists(ctx, s.Bucket)
	return err
}
