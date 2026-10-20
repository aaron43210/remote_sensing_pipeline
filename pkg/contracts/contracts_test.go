package contracts

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

// Every field the Go structs read or write must exist in the pydantic-generated
// schema (contracts/*.schema.json), so the two languages cannot drift apart.
func check(t *testing.T, topic string, v any) {
	raw, err := os.ReadFile("../../contracts/" + topic + ".schema.json")
	if err != nil {
		t.Fatalf("schema for %s: %v", topic, err)
	}
	var schema struct {
		Properties map[string]any `json:"properties"`
	}
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	ty := reflect.TypeOf(v)
	for i := 0; i < ty.NumField(); i++ {
		name := strings.Split(ty.Field(i).Tag.Get("json"), ",")[0]
		if _, ok := schema.Properties[name]; !ok {
			t.Errorf("%s: Go field %q not in schema", topic, name)
		}
	}
}

func TestContractsMatchSchemas(t *testing.T) {
	check(t, TopicIngestRequests, IngestRequest{})
	check(t, TopicJobStatus, JobStatus{})
	check(t, TopicMLResults, MLResult{})
}
