--
-- PostgreSQL database dump
--

SET statement_timeout = 0;
SET lock_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET client_min_messages = warning;

SET search_path = public, pg_catalog;

SET default_tablespace = '';

SET default_with_oids = false;

--
-- Name: fixture_market_summaries; Type: TABLE; Schema: public; Owner: patrickmckelvy; Tablespace: 
--

CREATE TABLE fixture_market_summaries (
    id integer NOT NULL,
    marketname character varying(12) NOT NULL,
    volume double precision NOT NULL,
    last double precision NOT NULL,
    basevolume double precision NOT NULL,
    bid double precision NOT NULL,
    ask double precision NOT NULL,
    openbuyorders integer NOT NULL,
    opensellorders integer NOT NULL,
    saved_timestamp timestamp without time zone NOT NULL,
    ticker_nonce integer NOT NULL
);


ALTER TABLE fixture_market_summaries OWNER TO patrickmckelvy;

--
-- Name: market_summaries_id_seq; Type: SEQUENCE; Schema: public; Owner: patrickmckelvy
--

CREATE SEQUENCE market_summaries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE market_summaries_id_seq OWNER TO patrickmckelvy;

--
-- Name: market_summaries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: patrickmckelvy
--

ALTER SEQUENCE market_summaries_id_seq OWNED BY fixture_market_summaries.id;


--
-- Name: id; Type: DEFAULT; Schema: public; Owner: patrickmckelvy
--

ALTER TABLE ONLY fixture_market_summaries ALTER COLUMN id SET DEFAULT nextval('market_summaries_id_seq'::regclass);


--
-- Name: market_summaries_pkey; Type: CONSTRAINT; Schema: public; Owner: patrickmckelvy; Tablespace: 
--

ALTER TABLE ONLY fixture_market_summaries
    ADD CONSTRAINT market_summaries_pkey PRIMARY KEY (id);


--
-- Name: marketname_idx; Type: INDEX; Schema: public; Owner: patrickmckelvy; Tablespace: 
--

CREATE INDEX marketname_idx ON fixture_market_summaries USING btree (marketname);


--
-- Name: ticker_nonce_idx; Type: INDEX; Schema: public; Owner: patrickmckelvy; Tablespace: 
--

CREATE INDEX ticker_nonce_idx ON fixture_market_summaries USING btree (ticker_nonce);


--
-- PostgreSQL database dump complete
--
