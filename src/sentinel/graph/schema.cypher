// Sentinel Day One - Neo4j schema
// Run this on a fresh instance: cypher-shell -u neo4j -p sentinel-dev -f schema.cypher
// Idempotent: every constraint/index uses IF NOT EXISTS

// ─── constraints (uniqueness) ────────────────────────────────────────────────

CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT team_id IF NOT EXISTS FOR (t:Team) REQUIRE t.id IS UNIQUE;
CREATE CONSTRAINT service_id IF NOT EXISTS FOR (s:Service) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT repo_id IF NOT EXISTS FOR (r:Repository) REQUIRE r.full_name IS UNIQUE;
CREATE CONSTRAINT pr_id IF NOT EXISTS FOR (p:PullRequest) REQUIRE p.global_id IS UNIQUE;
CREATE CONSTRAINT ticket_id IF NOT EXISTS FOR (t:Ticket) REQUIRE t.id IS UNIQUE;
CREATE CONSTRAINT adr_id IF NOT EXISTS FOR (a:ADR) REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT conf_id IF NOT EXISTS FOR (c:ConfluencePage) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT slack_id IF NOT EXISTS FOR (s:SlackThread) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT incident_id IF NOT EXISTS FOR (i:Incident) REQUIRE i.id IS UNIQUE;
CREATE CONSTRAINT runbook_id IF NOT EXISTS FOR (r:Runbook) REQUIRE r.id IS UNIQUE;
CREATE CONSTRAINT alert_id IF NOT EXISTS FOR (a:AlertRule) REQUIRE a.id IS UNIQUE;

// ─── indexes for retrieval ──────────────────────────────────────────────────

CREATE INDEX person_email IF NOT EXISTS FOR (p:Person) ON (p.email);
CREATE INDEX service_owner IF NOT EXISTS FOR (s:Service) ON (s.owner_team);
CREATE INDEX repo_team IF NOT EXISTS FOR (r:Repository) ON (r.owner_team);
CREATE INDEX pr_state IF NOT EXISTS FOR (p:PullRequest) ON (p.state);
CREATE INDEX ticket_status IF NOT EXISTS FOR (t:Ticket) ON (t.status);
CREATE INDEX incident_date IF NOT EXISTS FOR (i:Incident) ON (i.started_at);
CREATE INDEX slack_channel IF NOT EXISTS FOR (s:SlackThread) ON (s.channel);

// ─── vector indexes for embedding-based retrieval ────────────────────────────
// Dimension 3072 = text-embedding-3-large. Change to 1536 if using -small.

CREATE VECTOR INDEX slack_embeddings IF NOT EXISTS
FOR (s:SlackThread) ON (s.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX pr_embeddings IF NOT EXISTS
FOR (p:PullRequest) ON (p.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX adr_embeddings IF NOT EXISTS
FOR (a:ADR) ON (a.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX conf_embeddings IF NOT EXISTS
FOR (c:ConfluencePage) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX ticket_embeddings IF NOT EXISTS
FOR (t:Ticket) ON (t.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

// ─── reference: edge types used ──────────────────────────────────────────────
// (no schema enforcement, Neo4j relationships are dynamic - documented here)
//
// (:Person)-[:BELONGS_TO]->(:Team)
// (:Person)-[:AUTHORED]->(:PullRequest|:ADR|:ConfluencePage)
// (:Person)-[:RESPONDED_TO]->(:SlackThread)
// (:Team)-[:OWNS]->(:Service|:Repository|:Runbook|:AlertRule)
// (:Team)-[:CO_OWNS]->(:Repository)        // SRE + payments-team co-own paths
// (:Service)-[:DEPENDS_ON]->(:Service)
// (:Service)-[:DEPLOYED_FROM]->(:Repository)
// (:PullRequest)-[:CLOSES]->(:Ticket)
// (:PullRequest)-[:MODIFIES]->(:Repository)
// (:PullRequest)-[:REFERENCES]->(:ADR|:Ticket|:SlackThread)
// (:Ticket)-[:REFERENCES]->(:ADR|:Incident)
// (:ADR)-[:DECIDED_IN]->(:SlackThread)
// (:ADR)-[:TRIGGERED_BY]->(:Incident)
// (:Incident)-[:IMPACTED]->(:Service)
// (:Runbook)-[:DOCUMENTS]->(:Service)
// (:AlertRule)-[:MONITORS]->(:Service)
// (:SlackThread)-[:MENTIONS]->(:Service|:Person|:Repository|:Incident)
